"""HTTP control endpoints for MCSS: /stop and /restart on CONTROL_PORT."""

import asyncio
import hmac

from aiohttp import web

import config
from logger import logger


def _authorized(request: web.Request) -> bool:
    secret = str(getattr(config, "CONTROL_SECRET", "") or "")
    if not secret:
        return False

    provided = request.headers.get("X-API-Key") or request.headers.get("X-Secret") or ""
    auth = request.headers.get("Authorization", "")
    if not provided and auth.lower().startswith("bearer "):
        provided = auth[7:].strip()
    if not provided:
        provided = request.query.get("secret", "")

    return hmac.compare_digest(provided, secret)


async def start_control_server(on_action):
    """Start the HTTP server. on_action("stop"|"restart") runs in the bot loop."""
    app = web.Application()

    async def _dispatch(action: str):
        await asyncio.sleep(0.2)   # let the HTTP response flush first
        try:
            on_action(action)
        except Exception:
            logger.exception("Control action %r lỗi", action)

    async def _handle(request: web.Request, action: str):
        if not _authorized(request):
            return web.json_response({"ok": False, "error": "unauthorized"}, status=401)
        asyncio.create_task(_dispatch(action))
        return web.json_response({"ok": True, "action": action})

    async def stop(request: web.Request):
        return await _handle(request, "stop")

    async def restart(request: web.Request):
        return await _handle(request, "restart")

    app.router.add_route("*", "/stop", stop)
    app.router.add_route("*", "/restart", restart)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", config.CONTROL_PORT)
    await site.start()

    if not config.CONTROL_SECRET:
        logger.warning("CONTROL_SECRET đang trống; /stop và /restart sẽ luôn trả 401")
    logger.info(
        "Control server nghe ở 0.0.0.0:%s (/stop, /restart)",
        config.CONTROL_PORT,
    )
    return runner
