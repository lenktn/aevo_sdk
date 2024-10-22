import asyncio
import json
import random
import time
import traceback

import requests
import websockets
from eth_account import Account
from eth_hash.auto import keccak
from loguru import logger
from web3 import Web3

from eip712_structs import Address, Boolean, EIP712Struct, Uint, Bytes, make_domain

CONFIG = {
    "testnet": {
        "rest_url": "https://api-testnet.aevo.xyz",
        "ws_url": "wss://ws-testnet.aevo.xyz",
        "signing_domain": {
            "name": "Aevo Testnet",
            "version": "1",
            "chainId": "11155111",
        },
    },
    "mainnet": {
        "rest_url": "https://api.aevo.xyz",
        "ws_url": "wss://ws.aevo.xyz",
        "signing_domain": {
            "name": "Aevo Mainnet",
            "version": "1",
            "chainId": "1",
        },
    },
}

# Адреса для отправки и получения транзакции через смарт-контракты в разных сетях и уровнях блокчейна
ADDRESSES = {
    "testnet": {
        "l1_bridge": "0xb459023ECAf4ee7E55BEC136e592d2B7afF482E2",
        "l1_usdc": "0xcC3e3DBb31a7410e1dc5156593CdBFA0616BB309",
        "l2_withdraw_proxy": "0x870b65A0816B9e9A0dFCE08Fd18EFE20f245011f",
        "l2_usdc": "0x52623B37Ff81c53567D6D16fd94638734cCDCf27",
    },
    "mainnet": {
        "l1_bridge": "0x4082C9647c098a6493fb499EaE63b5ce3259c574",
        "l1_usdc": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
        "l2_withdraw_proxy": "0x4d44B9AbB13C80d2E376b7C5c982aa972239d845",
        "l2_usdc": "0x643aaB1618c600229785A5E06E4b2d13946F7a1A",
    },
}

# Структура данных для ордера (реализует спецификацию EIP-712, которая определяет структурированные данные для подписи в Ethereum)
class Order(EIP712Struct):
    maker = Address()
    isBuy = Boolean()
    limitPrice = Uint(256)
    amount = Uint(256)
    salt = Uint(256)
    instrument = Uint(256)
    timestamp = Uint(256)

# Структура данных для вывода средств (реализует спецификацию EIP-712, которая определяет структурированные данные для подписи в Ethereum)
class Withdraw(EIP712Struct):
    collateral = Address()
    to = Address()
    amount = Uint(256)
    salt = Uint(256)
    data = Bytes(32)

# Основной класс для взаимодействия с API
class AevoClient:
    def __init__(
        self,
        signing_key="",  # Ключ для подписи, который используется для аутентификации при отправке запросов
        wallet_address="",  # Адрес кошелька
        wallet_private_key="",  # Приватный ключ кошелька, необходимый для операций вывода средств
        api_key="",  # Ключ API для доступа к ресурсам API
        api_secret="",  # Секретный ключ для подписи запросов
        env="mainnet",
        rest_headers={},
    ):
        self.signing_key = signing_key
        self.wallet_address = wallet_address
        self.wallet_private_key = wallet_private_key
        self.api_key = api_key
        self.api_secret = api_secret
        self.connection = None
        self.client = requests
        self.rest_headers = {
            "AEVO-KEY": api_key,
            "AEVO-SECRET": api_secret,
        }
        self.extra_headers = None
        self.rest_headers.update(rest_headers)

        if (env != "testnet") and (env != "mainnet"):
            raise ValueError("env must either be 'testnet' or 'mainnet'")
        self.env = env

    # Получает адрес кошелька на основе signing_key
    @property
    def address(self):
        return Account.from_key(self.signing_key).address

    # Получает URL для REST API в зависимости от среды
    @property
    def rest_url(self):
        return CONFIG[self.env]["rest_url"]

    # Получает URL для WebSocket API в зависимости от среды
    @property
    def ws_url(self):
        return CONFIG[self.env]["ws_url"]

    # Домен для подписи запросов
    @property
    def signing_domain(self):
        return CONFIG[self.env]["signing_domain"]

    # Получаем instrument_id для тикера (asset - токен)
    def instrument_id(self, asset):
        req = self.client.get(f"{self.rest_url}/instrument/{asset}-PERP")
        data = req.json()
        return data['instrument_id']

    # Соединение с WebSocket

    # Открываем соединение с WebSocket, аутентифицируем пользователя
    async def open_connection(self, extra_headers={}):
        try:
            logger.info("Opening Aevo websocket connection...")

            self.connection = await websockets.connect(
                self.ws_url, ping_interval=None, extra_headers=extra_headers
            )
            if not self.extra_headers:
                self.extra_headers = extra_headers

            if self.api_key and self.wallet_address:
                logger.debug(f"Connecting to {self.ws_url}...")
                await self.connection.send(
                    json.dumps(
                        {
                            "id": 1,
                            "op": "auth",
                            "data": {
                                "key": self.api_key,
                                "secret": self.api_secret,
                            },
                        }
                    )
                )

            # Sleep as authentication takes some time, especially slower on testnet
            await asyncio.sleep(1)
        except Exception as e:
            logger.error("Error thrown when opening connection")
            logger.error(e)
            logger.error(traceback.format_exc())
            await asyncio.sleep(10)  # Don't retry straight away

    # Пытаемся восстановить соединение с WebSocket, если оно было закрыто
    async def reconnect(self):
        logger.info("Trying to reconnect Aevo websocket...")
        await self.close_connection()
        await self.open_connection(self.extra_headers)

    # Закрываем текущее соединение с WebSocket
    async def close_connection(self):
        try:
            logger.info("Closing connection...")
            await self.connection.close()
            logger.info("Connection closed")
        except Exception as e:
            logger.error("Error thrown when closing connection")
            logger.error(e)
            logger.error(traceback.format_exc())

    # Читаем сообщения из WebSocket и обрабатываем отключения, автоматически восстанавливая соединение
    async def read_messages(self, read_timeout=0.1, backoff=0.1, on_disconnect=None):
        while True:
            try:
                message = await asyncio.wait_for(
                    self.connection.recv(), timeout=read_timeout
                )
                yield message
            except (
                websockets.exceptions.ConnectionClosedError,
                websockets.exceptions.ConnectionClosedOK,
            ) as e:
                if on_disconnect:
                    on_disconnect()
                logger.error("Aevo websocket connection close")
                logger.error(e)
                logger.error(traceback.format_exc())
                await self.reconnect()
            except asyncio.TimeoutError:
                await asyncio.sleep(backoff)
            except Exception as e:
                logger.error(e)
                logger.error(traceback.format_exc())
                await asyncio.sleep(1)

    # Отправляем данные через WebSocket
    async def send(self, data):
        try:
            await self.connection.send(data)
        except websockets.exceptions.ConnectionClosedError as e:
            logger.debug("Restarted Aevo websocket connection")
            await self.reconnect()
            await self.connection.send(data)
        except:
            await self.reconnect()

    # Методы для работы с REST API

    # Получаем цену MarkPrice для указанного актива на текущий момент времени
    def get_index(self, asset):
        req = self.client.get(f"{self.rest_url}/index?asset={asset}")
        data = req.json()
        return data

    # Получаем список рынков для указанного актива
    def get_markets(self, asset):
        req = self.client.get(f"{self.rest_url}/markets?asset={asset}")
        data = req.json()
        return data

    # Получаем orderbook (bid)
    def get_orderbook(self, asset):
        req = self.client.get(f"{self.rest_url}/orderbook?instrument_name={asset}-PERP")
        data = req.json()
        return data

    # Создаем и отправляем ордер через REST API
    def rest_create_order(
        self, instrument_id, is_buy, limit_price, quantity, post_only=True
    ):
        data, order_id = self.create_order_rest_json(
            int(instrument_id), is_buy, limit_price, quantity, post_only
        )
        logger.info(data)
        req = self.client.post(
            f"{self.rest_url}/orders", json=data, headers=self.rest_headers
        )
        try:
            return req.json()
        except:
            return req.text()

    # Создаем и отправляем рыночный ордер
    def rest_create_market_order(self, instrument_id, is_buy, quantity):
        limit_price = 0
        if is_buy:
            limit_price = 2**256 - 1

        data, order_id = self.create_order_rest_json(
            int(instrument_id),
            is_buy,
            limit_price,
            quantity,
            price_decimals=1,
            post_only=False,
        )

        req = self.client.post(
            f"{self.rest_url}/orders", json=data, headers=self.rest_headers
        )
        return req.json()

    # Отменяем ордер по его идентификатору
    def rest_cancel_order(self, order_id):
        req = self.client.delete(
            f"{self.rest_url}/orders/{order_id}", headers=self.rest_headers
        )
        logger.info(req.json())
        return req.json()

    # Получаем информацию о счете
    def rest_get_account(self):
        req = self.client.get(f"{self.rest_url}/account", headers=self.rest_headers)
        return req.json()

    # Получаем информацию о портфеле
    def rest_get_portfolio(self):
        req = self.client.get(f"{self.rest_url}/portfolio", headers=self.rest_headers)
        return req.json()

    # Получаем открытые ордера
    def rest_get_open_orders(self):
        req = self.client.get(
            f"{self.rest_url}/orders", json={}, headers=self.rest_headers
        )
        return req.json()

    # Отменяем все ордера
    def rest_cancel_all_orders(
        self,
        instrument_type=None,
        asset=None,
    ):
        body = {}
        if instrument_type:
            body["instrument_type"] = instrument_type

        if asset:
            body["asset"] = asset

        req = self.client.delete(
            f"{self.rest_url}/orders-all", json=body, headers=self.rest_headers
        )
        return req.json()

    # Выполняем вывод средств из кошелька
    def withdraw(
        self,
        amount,
        collateral=None,
        to=None,
        data=None,
        amount_decimals=10**6,
    ):
        if collateral == None:
            collateral = ADDRESSES[self.env]["l2_usdc"]

        if to == None:
            to = ADDRESSES[self.env]["l2_withdraw_proxy"]

        data, withdraw_id = self.create_withdraw(
            collateral, to, amount, data, amount_decimals
        )
        logger.info(withdraw_id)
        logger.info(data)
        req = self.client.post(
            f"{self.rest_url}/withdraw", json=data, headers=self.rest_headers
        )
        try:
            return req.json()
        except:
            return req.text()

    # Public WS Subscriptions
    # Подписываемся на обновления тикеров для указанного актива

    # bid ask, примеры данных: book-ticker:ALT:PERPETUAL, book-ticker:AEVO-PERP, book-ticker:BTC-01NOV24-58000-P (посм-ть ticker-500ms есть фандинг)
    async def subscribe_tickers(self, asset):
        await self.send(
            json.dumps(
                {
                    "op": "subscribe",
                    "data": [f"book-ticker:{asset}:PERPETUAL"],
                }
            )
        )

    # Подписаться на канал ?
    async def subscribe_ticker(self, channel):
        msg = json.dumps(
            {
                "op": "subscribe",
                "data": [channel],
            }
        )
        await self.send(msg)

    # Получить mark price, index price
    async def subscribe_markprice(self, asset):
        await self.send(
            json.dumps(
                {
                    "op": "subscribe",
                    "data": [f"markprice:{asset}:PERPETUAL"],
                }
            )
        )

    # Подписываемся на обновления книги ордеров для указанного инструмента (bid, ask) 100ms или 500ms
    async def subscribe_orderbook(self, instrument_name):
        await self.send(
            json.dumps(
                {
                    "op": "subscribe",
                    "data": [f"orderbook-500ms:{instrument_name}-PERP"],
                }
            )
        )

    # Подписываемся на обновления сделок для указанного инструмента (-PERP)
    async def subscribe_trades(self, instrument_name):
        await self.send(
            json.dumps(
                {
                    "op": "subscribe",
                    "data": [f"trades:{instrument_name}"],
                }
            )
        )

    # Получить index price
    async def subscribe_index(self, asset):
        await self.send(
            json.dumps(
                {"op": "subscribe",
                 "data": [f"index:{asset}"]
                 }
            )
        )

    # Private WS Subscriptions
    # Выставленные ордера
    async def subscribe_orders(self):
        payload = {
            "op": "subscribe",
            "data": ["orders"],
        }
        await self.send(json.dumps(payload))

    async def subscribe_fills(self, asset):
        payload = {
            "op": "subscribe",
            "data": [f"fills:{asset}-PERP"],
        }
        await self.send(json.dumps(payload))

    # Создаем JSON-структуру для отправки ордера через WebSocket
    def create_order_ws_json(
        self,
        instrument_id,
        is_buy,
        limit_price,
        quantity,
        post_only=True,
        mmp=True,
        price_decimals=10**6,
        amount_decimals=10**6,
    ):
        timestamp = int(time.time())
        salt, signature, order_id = self.sign_order(
            instrument_id=instrument_id,
            is_buy=is_buy,
            limit_price=limit_price,
            quantity=quantity,
            timestamp=timestamp,
            price_decimals=price_decimals,
        )

        payload = {
            "instrument": instrument_id,
            "maker": self.wallet_address,
            "is_buy": is_buy,
            "amount": str(int(round(quantity * amount_decimals, is_buy))),
            "limit_price": str(int(round(limit_price * price_decimals, is_buy))),
            "salt": str(salt),
            "signature": signature,
            "post_only": post_only,
            "mmp": mmp,
            "timestamp": timestamp,
        }
        return payload, order_id

    # Создаем JSON-структуру для отправки ордера через rest api
    def create_order_rest_json(
        self,
        instrument_id,  # id токена на бирже
        is_buy,  # True - покупка/False - продажа
        limit_price,  # предельная цена ордера
        quantity,  # кол-во
        post_only=True,  # Post-only=True - лимитный ордер добавится в книгу заявок и не будет исполнен сразу
        reduce_only=False,  # True: Ордер будет уменьшать открытую позицию, но не сможет увеличить её или открыть новую; False: Ордер может вести себя как обычный ордер, т.е. может как уменьшить, так и увеличить позицию или открыть новую
        close_position=False,  # True: Ордер предназначен для полного закрытия текущей позиции. Используется в ордерах типа Take Profit (TP) и Stop Loss (SL); False: Это стандартное поведение, при котором ордер может как закрывать, так и открывать новую позицию или изменять текущую позицию
        price_decimals=10**6,  #
        amount_decimals=10**6,  #
        trigger=None,  # цена срабатывания стоп-ордера
        stop=None,  # для стоп-ордеров
    ):
        timestamp = int(time.time())
        # формирование подписи ордера
        salt, signature, order_id = self.sign_order(
            instrument_id=instrument_id,
            is_buy=is_buy,
            limit_price=limit_price,
            quantity=quantity,
            timestamp=timestamp,
            price_decimals=price_decimals,
        )
        payload = {
            "maker": self.wallet_address,
            "is_buy": is_buy,
            "instrument": instrument_id,
            "limit_price": str(int(round(limit_price * price_decimals, is_buy))),
            "amount": str(int(round(quantity * amount_decimals, is_buy))),
            "salt": str(salt),
            "signature": signature,
            "post_only": post_only,
            "reduce_only": reduce_only,
            "close_position": close_position,
            "timestamp": timestamp,
        }
        if trigger and stop:
            payload["trigger"] = trigger
            payload["stop"] = stop

        return payload, order_id

    # Отправляет запрос на создание ордера через WebSocket
    async def create_order(
        self,
        instrument_id,
        is_buy,
        limit_price,
        quantity,
        post_only=True,
        id=None,
        mmp=True,
    ):
        data, order_id = self.create_order_ws_json(
            instrument_id=int(instrument_id),
            is_buy=is_buy,
            limit_price=limit_price,
            quantity=quantity,
            post_only=post_only,
            mmp=mmp,
        )
        payload = {"op": "create_order", "data": data}
        if id:
            payload["id"] = id

        logger.info(payload)
        await self.send(json.dumps(payload))

        return order_id

    # Редактируем ордер через WebSocket
    async def edit_order(
        self,
        order_id,
        instrument_id,
        is_buy,
        limit_price,
        quantity,
        id=None,
        post_only=True,
        mmp=True,
    ):
        timestamp = int(time.time())
        instrument_id = int(instrument_id)
        salt, signature, new_order_id = self.sign_order(
            instrument_id=instrument_id,
            is_buy=is_buy,
            limit_price=limit_price,
            quantity=quantity,
            timestamp=timestamp,
        )
        payload = {
            "op": "edit_order",
            "data": {
                "order_id": order_id,
                "instrument": instrument_id,
                "maker": self.wallet_address,
                "is_buy": is_buy,
                "amount": str(int(round(quantity * 10**6, is_buy))),
                "limit_price": str(int(round(limit_price * 10**6, is_buy))),
                "salt": str(salt),
                "signature": signature,
                "post_only": post_only,
                "mmp": mmp,
                "timestamp": timestamp,
            },
        }

        if id:
            payload["id"] = id

        logger.info(payload)
        await self.send(json.dumps(payload))

        return new_order_id

    # Закрываем ордер
    async def cancel_order(self, order_id):
        if not order_id:
            return

        payload = {"op": "cancel_order", "data": {"order_id": order_id}}
        logger.info(payload)
        await self.send(json.dumps(payload))

    async def cancel_all_orders(self):
        payload = {"op": "cancel_all_orders", "data": {}}
        await self.send(json.dumps(payload))

    # Подписание ордера
    def sign_order(
        self,
        instrument_id,
        is_buy,
        limit_price,
        quantity,
        timestamp,
        price_decimals=10**6,
        amount_decimals=10**6,
    ):
        salt = random.randint(0, 10**10)  # We just need a large enough number

        order_struct = Order(
            maker=self.wallet_address,  # The wallet"s main address
            isBuy=is_buy,
            limitPrice=int(round(limit_price * price_decimals, is_buy)),
            amount=int(round(quantity * amount_decimals, is_buy)),
            salt=salt,
            instrument=instrument_id,
            timestamp=timestamp,
        )
        logger.info(self.signing_domain)
        domain = make_domain(**self.signing_domain)
        signable_bytes = keccak(order_struct.signable_bytes(domain=domain))
        return (
            salt,
            Account._sign_hash(signable_bytes, self.signing_key).signature.hex(),
            f"0x{signable_bytes.hex()}",
        )

    # Создаем запрос на вывод средств
    def create_withdraw(self, collateral, to, amount, data, amount_decimals):
        if data == None:
            data = keccak(bytearray()).hex()
        salt, signature, withdraw_id = self.sign_withdraw(
            collateral=collateral,  # адрес кошелька для вывода
            to=to,  # тип обеспечения (токен или валюта), Ethereum-адрес залогового актива
            amount=amount,  # сумма в USDC. In 6 decimals fixed number
            data=data,  # подпись
            amount_decimals=amount_decimals,
        )
        payload = {
            "account": self.wallet_address,
            "collateral": collateral,
            "to": to,
            "amount": int(round(amount * amount_decimals)),
            "salt": salt,
            "signature": signature,
        }
        if data != None:
            payload["data"] = data

        return payload, withdraw_id

    # Подписываем транзакцию вывода средств
    def sign_withdraw(self, collateral, to, amount, data, amount_decimals):
        salt = random.randint(0, 10**10)  # We just need a large enough number

        withdraw_struct = Withdraw(
            to=to,
            collateral=collateral,
            amount=int(round(amount * amount_decimals)),
            salt=salt,
            data=data,
        )
        logger.info(self.signing_domain)
        domain = make_domain(**self.signing_domain)
        signable_bytes = keccak(withdraw_struct.signable_bytes(domain=domain))
        return (
            salt,
            Account._sign_hash(signable_bytes, self.wallet_private_key).signature.hex(),
            f"0x{signable_bytes.hex()}",
        )


async def main():
    # The following values which are used for authentication on private endpoints, can be retrieved from the Aevo UI
    aevo = AevoClient(
        signing_key="e7318bb3d5d3df5b92c1e6b6b5204c2145a019f9a38498a8c5791afe470cbe84",
        wallet_address="0x635693DC1612faA23c993C81214748a390b9787d",
        api_key="91EyJB9QAiZHvrsAzg3uQpoaHA59GYVX",
        api_secret="e24d0129699f60f9e5602dc9324c0e90510a2af574de2b7d3b71922823941156",
        env="mainnet",
    )

    markets = aevo.get_markets("AEVO")
    logger.info(markets)

    await aevo.open_connection()

    async for msg in aevo.read_messages():
        logger.info(msg)


if __name__ == "__main__":
    asyncio.run(main())
