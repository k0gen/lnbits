import asyncio
import json
from typing import Dict, Optional

import httpx
from loguru import logger

from lnbits.settings import get_wallet_class, settings
from lnbits.tasks import SseListenersDict, register_invoice_listener

from . import db
from .crud import get_balance_notify, get_wallet
from .models import Payment
from .services import get_balance_delta, switch_to_voidwallet, websocketUpdater

api_invoice_listeners: Dict[str, asyncio.Queue] = SseListenersDict(
    "api_invoice_listeners"
)

killswitch: Optional[asyncio.Task] = None
watchdog: Optional[asyncio.Task] = None


async def register_killswitch():
    """
    Registers a killswitch which will check lnbits-status repository
    for a signal from LNbits and will switch to VoidWallet if the killswitch is triggered.
    """
    logger.debug("Starting killswitch task")
    global killswitch
    killswitch = asyncio.create_task(killswitch_task())


async def unregister_killswitch():
    """
    Unregisters a killswitch taskl
    """
    global killswitch
    if killswitch:
        logger.debug("Stopping killswitch task")
        killswitch.cancel()


async def killswitch_task():
    while True:
        WALLET = get_wallet_class()
        if settings.lnbits_killswitch and WALLET.__class__.__name__ != "VoidWallet":
            with httpx.Client() as client:
                try:
                    r = client.get(settings.lnbits_status_manifest, timeout=4)
                    r.raise_for_status()
                    if r.status_code == 200:
                        ks = r.json().get("killswitch")
                        if ks and ks == 1:
                            logger.error(
                                "Switching to VoidWallet. Killswitch triggered."
                            )
                            await switch_to_voidwallet()
                except (httpx.ConnectError, httpx.RequestError):
                    logger.error(
                        f"Cannot fetch lnbits status manifest. {settings.lnbits_status_manifest}"
                    )
        await asyncio.sleep(settings.lnbits_killswitch_interval * 60)


async def register_watchdog():
    """
    Registers a watchdog which will check lnbits balance and nodebalance
    and will switch to VoidWallet if the watchdog delta is reached.
    """
    # TODO: implement watchdog porperly
    # logger.debug("Starting watchdog task")
    # global watchdog
    # watchdog = asyncio.create_task(watchdog_task())


async def unregister_watchdog():
    """
    Unregisters a watchdog task
    """
    global watchdog
    if watchdog:
        logger.debug("Stopping watchdog task")
        watchdog.cancel()


async def watchdog_task():
    while True:
        WALLET = get_wallet_class()
        if settings.lnbits_watchdog and WALLET.__class__.__name__ != "VoidWallet":
            try:
                delta, *_ = await get_balance_delta()
                logger.debug(f"Running watchdog task. current delta: {delta}")
                if delta + settings.lnbits_watchdog_delta <= 0:
                    logger.error(f"Switching to VoidWallet. current delta: {delta}")
                    await switch_to_voidwallet()
            except Exception as e:
                logger.error("Error in watchdog task", e)
        await asyncio.sleep(settings.lnbits_watchdog_interval * 60)


async def register_task_listeners():
    """
    Registers an invoice listener queue for the core tasks.
    Incoming payaments in this queue will eventually trigger the signals sent to all other extensions
    and fulfill other core tasks such as dispatching webhooks.
    """
    invoice_paid_queue = asyncio.Queue(5)
    # we register invoice_paid_queue to receive all incoming invoices
    register_invoice_listener(invoice_paid_queue, "core/tasks.py")
    # register a worker that will react to invoices
    asyncio.create_task(wait_for_paid_invoices(invoice_paid_queue))


async def wait_for_paid_invoices(invoice_paid_queue: asyncio.Queue):
    """
    This worker dispatches events to all extensions, dispatches webhooks and balance notifys.
    """
    while True:
        payment = await invoice_paid_queue.get()
        logger.trace("received invoice paid event")
        # send information to sse channel
        await dispatch_api_invoice_listeners(payment)
        wallet = await get_wallet(payment.wallet_id)
        if wallet:
            await websocketUpdater(
                payment.wallet_id,
                json.dumps(
                    {
                        "wallet_balance": wallet.balance or None,
                        "payment": payment.dict(),
                    }
                ),
            )
        # dispatch webhook
        if payment.webhook and not payment.webhook_status:
            await dispatch_webhook(payment)

        # dispatch balance_notify
        url = await get_balance_notify(payment.wallet_id)
        if url:
            async with httpx.AsyncClient() as client:
                try:
                    r = await client.post(url, timeout=4)
                    await mark_webhook_sent(payment, r.status_code)
                except (httpx.ConnectError, httpx.RequestError):
                    pass


async def dispatch_api_invoice_listeners(payment: Payment):
    """
    Emits events to invoice listener subscribed from the API.
    """
    for chan_name, send_channel in api_invoice_listeners.items():
        try:
            logger.debug(f"sending invoice paid event to {chan_name}")
            send_channel.put_nowait(payment)
        except asyncio.QueueFull:
            logger.error(f"removing sse listener {send_channel}:{chan_name}")
            api_invoice_listeners.pop(chan_name)


async def dispatch_webhook(payment: Payment):
    """
    Dispatches the webhook to the webhook url.
    """
    async with httpx.AsyncClient() as client:
        data = payment.dict()
        try:
            logger.debug("sending webhook", payment.webhook)
            r = await client.post(payment.webhook, json=data, timeout=40)  # type: ignore
            await mark_webhook_sent(payment, r.status_code)
        except (httpx.ConnectError, httpx.RequestError):
            await mark_webhook_sent(payment, -1)


async def mark_webhook_sent(payment: Payment, status: int) -> None:
    await db.execute(
        """
        UPDATE apipayments SET webhook_status = ?
        WHERE hash = ?
        """,
        (status, payment.payment_hash),
    )
