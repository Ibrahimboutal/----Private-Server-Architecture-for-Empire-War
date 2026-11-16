"""Async TCP entry point. No upstream endpoints or live-server forwarding."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import logging
from pathlib import Path
import time

from .auth.session_manager import ConnectionContext, SessionManager
from .config import ServerConfig
from .economy.resource_engine import CityResource, ResourceEngine
from .economy.gameplay_engine import GameplayEngine
from .economy.market_engine import MarketEngine, SimulatedMarketDisconnect
from .network_isolation import install_loopback_guard
from .protocol.frame_parser import FrameCodec, ProtocolError
from .protocol.packet_router import PacketRouter

LOGGER = logging.getLogger(__name__)


class GameServer:
    """Bounded, sequential per-connection requests with independent DB transactions."""

    def __init__(self, config: ServerConfig, router: PacketRouter) -> None:
        self.config, self.router = config, router
        self.codec = FrameCodec(max_frame_bytes=config.max_frame_bytes,
                                max_decoded_bytes=config.max_decoded_bytes)
        self._server: asyncio.Server | None = None
        self._clients: set[asyncio.Task[None]] = set()
        self._closing = False
        self._queue_worker: asyncio.Task[None] | None = None

    async def start(self, *, port: int | None = None) -> int:
        """Start on configured port; port=0 is reserved for isolated test fixtures."""
        if self._server is not None:
            raise RuntimeError("server already started")
        self._closing = False
        self._server = await asyncio.start_server(
            self._accept, self.config.host, self.config.port if port is None else port,
            limit=self.config.max_frame_bytes + 5, backlog=self.config.max_connections,
        )
        if self.router.gameplay is not None:
            self._queue_worker = asyncio.create_task(self._advance_queues(), name="local-gameplay-queues")
        return self._server.sockets[0].getsockname()[1]

    async def _advance_queues(self) -> None:
        """Queries also settle work; this worker handles otherwise idle clients."""
        assert self.router.gameplay is not None
        while True:
            try:
                await self.router.gameplay.settle_due()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                LOGGER.error("Queue advancement failed (%s)", type(exc).__name__)
            await asyncio.sleep(1)

    async def _accept(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.current_task()
        assert task is not None
        if self._closing or len(self._clients) >= self.config.max_connections:
            writer.close()
            with contextlib.suppress(ConnectionError):
                await writer.wait_closed()
            return
        self._clients.add(task)
        context = ConnectionContext()
        window, count = time.monotonic(), 0
        try:
            while True:
                async with asyncio.timeout(self.config.read_timeout_seconds):
                    frame = await self.codec.read_frame(reader)
                if frame is None:
                    break
                now = time.monotonic()
                if now - window >= 1.0:
                    window, count = now, 0
                count += 1
                if count > self.config.requests_per_second:
                    break
                response = await self.router.dispatch(frame.payload, context, frame_metadata={
                    "packet_type": frame.packet_type, "wire_length": frame.wire_length,
                    "plaintext_length": frame.plaintext_length})
                writer.write(self.codec.encode_payload(response))
                async with asyncio.timeout(self.config.read_timeout_seconds):
                    await writer.drain()
        except SimulatedMarketDisconnect as exc:
            LOGGER.info("Bounded local market simulation; closing one connection")
            await asyncio.sleep(exc.delay_ms / 1000)
        except (ProtocolError, TimeoutError, ConnectionError, asyncio.IncompleteReadError):
            LOGGER.info("Closed invalid, disconnected, or idle local connection")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Exception class only: never echo packets, credentials or DB contents.
            LOGGER.error("Local request failed (%s)", type(exc).__name__)
        finally:
            self.router.sessions.revoke(context.session_id)
            writer.close()
            with contextlib.suppress(ConnectionError, TimeoutError):
                async with asyncio.timeout(2):
                    await writer.wait_closed()
            self._clients.discard(task)

    async def close(self) -> None:
        self._closing = True
        server, self._server = self._server, None
        if server is not None:
            server.close()
        if self._queue_worker is not None:
            self._queue_worker.cancel()
            await asyncio.gather(self._queue_worker, return_exceptions=True)
            self._queue_worker = None
        clients = tuple(self._clients)
        for task in clients:
            task.cancel()
        if clients:
            await asyncio.gather(*clients, return_exceptions=True)
        # Python 3.12+ wait_closed also waits for active client transports.
        # Finish those handlers before waiting for all server transports.
        if server is not None:
            await server.wait_closed()

    async def serve_forever(self) -> None:
        if self._server is None:
            raise RuntimeError("server is not started")
        await self._server.serve_forever()


async def run(config: ServerConfig) -> None:
    engine = ResourceEngine(config.database, config.audit_database)
    await engine.initialize()
    await engine.seed_synthetic_user(config.synthetic_passport, config.synthetic_password,
        CityResource(cid=1, food=10000.0,
                     wood=178.0 if config.gameplay_scenario == "captured-quarry-shortage-v1" else 10000.0,
                     iron=10000.0, rock=10000.0,
                     gold=10000.0, people=1000, foodMax=100000.0, woodMax=100000.0,
                     ironMax=100000.0, rockMax=100000.0, goldMax=100000.0,
                     peopleMax=1000, morale=100, moraleStable=100,
                     lastupdate=time.time_ns() // 1_000_000_000))
    sessions = SessionManager(config.session_ttl_seconds, config.max_connections)
    gameplay = None
    if config.gameplay_scenario != "disabled":
        gameplay = GameplayEngine(engine, scenario_id=config.gameplay_scenario)
        await gameplay.seed_city(1, 1)
    market = None
    if config.market_scenario != "disabled":
        market = MarketEngine(engine, simulate_int32_crash=config.simulate_int32_crash,
                              overflow_breaker_threshold=config.overflow_breaker_threshold,
                              simulation_delay_ms=config.simulation_delay_ms)
    credentials = ((config.synthetic_passport, config.synthetic_password)
                   if config.synthetic_client_login else None)
    server = GameServer(config, PacketRouter(engine, sessions, gameplay, market,
                                            synthetic_credentials=credentials))
    port = await server.start()
    LOGGER.info("Research server listening on 127.0.0.1:%d; gameplay=%s", port, config.gameplay_scenario)
    if config.synthetic_client_login:
        LOGGER.info("Local client login bridge enabled; valid login packets select the synthetic research account")
    try:
        await server.serve_forever()
    finally:
        await server.close()


def main() -> None:
    parser = argparse.ArgumentParser(prog="empire-war-local", description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("config.yaml"))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        config = ServerConfig.load(args.config)
        install_loopback_guard()
        asyncio.run(run(config))
    except KeyboardInterrupt:
        pass
    except (ValueError, OSError) as exc:
        parser.exit(1, f"Startup failed ({type(exc).__name__}): {exc}\n")


if __name__ == "__main__":
    main()
