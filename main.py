import os
import time
from dotenv import load_dotenv
import MetaTrader5 as mt5
import pandas as pd

load_dotenv()

MT5_LOGIN = int(os.getenv("MT5_LOGIN", "0"))
MT5_PASSWORD = os.getenv("MT5_PASSWORD", "")
MT5_SERVER = os.getenv("MT5_SERVER", "")
MT5_PATH = os.getenv("MT5_PATH", "")

SYMBOL = os.getenv("SYMBOL", "EURUSD")
TIMEFRAME_NAME = os.getenv("TIMEFRAME", "M5")
LOT = float(os.getenv("LOT", "0.01"))
STOP_LOSS_POINTS = int(os.getenv("STOP_LOSS_POINTS", "300"))
TAKE_PROFIT_POINTS = int(os.getenv("TAKE_PROFIT_POINTS", "600"))
DEVIATION = int(os.getenv("DEVIATION", "20"))
MAGIC = int(os.getenv("MAGIC", "123456"))
MAX_OPEN_POSITIONS = int(os.getenv("MAX_OPEN_POSITIONS", "1"))
POLL_SECONDS = int(os.getenv("POLL_SECONDS", "10"))
DRY_RUN = os.getenv("DRY_RUN", "true").lower() == "true"

TIMEFRAME_MAP = {
    "M1": mt5.TIMEFRAME_M1,
    "M5": mt5.TIMEFRAME_M5,
    "M15": mt5.TIMEFRAME_M15,
    "M30": mt5.TIMEFRAME_M30,
    "H1": mt5.TIMEFRAME_H1,
    "H4": mt5.TIMEFRAME_H4,
    "D1": mt5.TIMEFRAME_D1,
}


def connect():
    if MT5_PATH:
        ok = mt5.initialize(path=MT5_PATH)
    else:
        ok = mt5.initialize()

    if not ok:
        print(f"[ERROR] initialize() failed: {mt5.last_error()}")
        return False

    authorized = mt5.login(login=MT5_LOGIN, password=MT5_PASSWORD, server=MT5_SERVER)
    if not authorized:
        print(f"[ERROR] login() failed: {mt5.last_error()}")
        mt5.shutdown()
        return False

    account = mt5.account_info()
    print("[INFO] Connected to MT5")
    print(f"[INFO] Account: {account.login if account else 'unknown'}")
    print(f"[INFO] Server: {account.server if account else 'unknown'}")
    return True


def ensure_symbol(symbol: str):
    info = mt5.symbol_info(symbol)
    if info is None:
        print(f"[ERROR] Symbol not found: {symbol}")
        return None

    if not info.visible:
        if not mt5.symbol_select(symbol, True):
            print(f"[ERROR] symbol_select failed for {symbol}")
            return None

    return info


def get_rates(symbol: str, timeframe, bars: int = 100):
    rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, bars)
    if rates is None:
        return None
    return pd.DataFrame(rates)


def simple_signal(symbol: str, timeframe):
    df = get_rates(symbol, timeframe, 100)
    if df is None or len(df) < 30:
        return None

    df["fast_ma"] = df["close"].rolling(10).mean()
    df["slow_ma"] = df["close"].rolling(20).mean()

    prev_fast = df["fast_ma"].iloc[-2]
    prev_slow = df["slow_ma"].iloc[-2]
    curr_fast = df["fast_ma"].iloc[-1]
    curr_slow = df["slow_ma"].iloc[-1]

    if prev_fast <= prev_slow and curr_fast > curr_slow:
        return "buy"

    if prev_fast >= prev_slow and curr_fast < curr_slow:
        return "sell"

    return None


def get_positions(symbol: str):
    positions = mt5.positions_get(symbol=symbol)
    return positions if positions else []


def open_trade(symbol: str, side: str):
    symbol_info = ensure_symbol(symbol)
    if symbol_info is None:
        return None

    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        print("[ERROR] Failed to get tick")
        return None

    point = symbol_info.point

    if side == "buy":
        order_type = mt5.ORDER_TYPE_BUY
        price = tick.ask
        sl = price - STOP_LOSS_POINTS * point
        tp = price + TAKE_PROFIT_POINTS * point
    elif side == "sell":
        order_type = mt5.ORDER_TYPE_SELL
        price = tick.bid
        sl = price + STOP_LOSS_POINTS * point
        tp = price - TAKE_PROFIT_POINTS * point
    else:
        print("[ERROR] Invalid side")
        return None

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": LOT,
        "type": order_type,
        "price": price,
        "sl": round(sl, symbol_info.digits),
        "tp": round(tp, symbol_info.digits),
        "deviation": DEVIATION,
        "magic": MAGIC,
        "comment": f"python-bot {side}",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }

    check = mt5.order_check(request)
    print(f"[INFO] order_check: {check}")

    if DRY_RUN:
        print("[DRY RUN] Order not sent.")
        return check

    result = mt5.order_send(request)
    print(f"[INFO] order_send: {result}")
    return result


def main():
    timeframe = TIMEFRAME_MAP.get(TIMEFRAME_NAME, mt5.TIMEFRAME_M5)

    if not connect():
        return

    try:
        while True:
            positions = get_positions(SYMBOL)
            signal = simple_signal(SYMBOL, timeframe)

            print(f"[INFO] Signal: {signal}, Open positions: {len(positions)}")

            if signal and len(positions) < MAX_OPEN_POSITIONS:
                open_trade(SYMBOL, signal)

            time.sleep(POLL_SECONDS)

    except KeyboardInterrupt:
        print("[INFO] Stopped by user")
    finally:
        mt5.shutdown()
        print("[INFO] MT5 connection closed")


if __name__ == "__main__":
    main()
