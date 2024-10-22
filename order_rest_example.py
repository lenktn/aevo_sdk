import asyncio

from loguru import logger

from aevo import AevoClient


async def main():
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

    # получаем id токена на бирже
    print(aevo.instrument_id('AEVO'))
    print(aevo.get_orderbook('AEVO'))

    # logger.info("Creating order...")
    # # ETH-PERP has an instrument id of 2054 on testnet, you can find the instrument id of other markets by looking at this endpoint: https://api-testnet.aevo.xyz/markets
    # response = aevo.rest_create_order(
    #     instrument_id=36711,
    #     is_buy=True,
    #     limit_price=1.3558,
    #     quantity=10,
    #     post_only=False,
    # )
    # logger.info(response)
    # if "error" in response:
    #     logger.error(f"Error creating order: {response['error']}")
    #     return


if __name__ == "__main__":
    asyncio.run(main())
