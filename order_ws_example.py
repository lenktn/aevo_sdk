import asyncio
import json

from loguru import logger

from aevo import AevoClient


async def main():
    # The following values which are used for authentication on private endpoints, can be retrieved from the Aevo UI
    aevo = AevoClient(
        signing_key="e7318bb3d5d3df5b92c1e6b6b5204c2145a019f9a38498a8c5791afe470cbe84",
        wallet_address="0x635693DC1612faA23c993C81214748a390b9787d",
        api_key="91EyJB9QAiZHvrsAzg3uQpoaHA59GYVX",
        api_secret="e24d0129699f60f9e5602dc9324c0e90510a2af574de2b7d3b71922823941156",
        env="mainnet",
    )

    if not aevo.signing_key:
        raise Exception(
            "Signing key is not set. Please set the signing key in the AevoClient constructor."
        )

    await aevo.open_connection()

    await aevo.subscribe_fills("ALT")

    # # посм-ть список каналов
    # await aevo.send(
    #     json.dumps(
    #         {
    #             "id": 1,
    #             "op": "channels"
    #         }
    #     )
    # )

    # await aevo.subscribe_orderbook("AEVO-PERP")
    # logger.info("Creating order...")
    # ETH-PERP has an instrument id of 2054 on testnet, you can find the instrument id of other markets by looking at this endpoint: https://api-testnet.aevo.xyz/markets
    # order_id = await aevo.create_order(
    #     instrument_id=2054,
    #     is_buy=True,
    #     limit_price=1200,
    #     quantity=0.01,
    #     post_only=False,
    # )

    # Wait for order to go through
    # await asyncio.sleep(1)

    # Edit the order price
    # NOTE: order id will change after editing
    # logger.info("Editing order...")
    # order_id = await aevo.edit_order(
    #     order_id=order_id,
    #     instrument_id=2054,
    #     is_buy=True,
    #     limit_price=1500,
    #     quantity=0.01,
    #     post_only=False,
    # )

    # logger.info("Cancelling order...")
    # order_id = await aevo.cancel_order(
    #     order_id=order_id,
    # )

    async for msg in aevo.read_messages():
        logger.info(msg)


if __name__ == "__main__":
    asyncio.run(main())
