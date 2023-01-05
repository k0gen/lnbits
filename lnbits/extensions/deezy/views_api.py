# views_api.py is for you API endpoints that could be hit by another service

# add your dependencies here

# import httpx
# (use httpx just like requests, except instead of response.ok there's only the
#  response.is_error that is its inverse)

from . import deezy_ext
from .models import (
    Token,
    LnToBtcSwap,
    BtcToLnSwap,
    UpdateLnToBtcSwap,
)

from .crud import (
    get_token,
    get_ln_to_btc,
    get_btc_to_ln,
    save_token,
    save_btc_to_ln,
    save_ln_to_btc,
    update_ln_to_btc
)


@deezy_ext.get("/api/v1/token")
async def api_deezy():
    rows = await get_token()
    return rows


@deezy_ext.get("/api/v1/ln-to-btc")
async def api_deezy():
    rows = await get_ln_to_btc()
    return rows


@deezy_ext.get("/api/v1/btc-to-ln")
async def api_deezy():
    rows = await get_btc_to_ln()
    return rows


@deezy_ext.post("/api/v1/store-token")
async def api_deezy(data: Token):
    await save_token(data)

    return data.deezy_token


@deezy_ext.post("/api/v1/store-ln-to-btc")
async def api_deezy(data: LnToBtcSwap):
    response = await save_ln_to_btc(data)

    return response


@deezy_ext.post("/api/v1/update-ln-to-btc")
async def api_deezy(data: UpdateLnToBtcSwap):
    response = await update_ln_to_btc(data)

    return response


@deezy_ext.post("/api/v1/store-btc-to-ln")
async def api_deezy(data: BtcToLnSwap):
    response = await save_btc_to_ln(data)

    return response
