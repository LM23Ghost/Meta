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

SYMBOLS_RAW = os.getenv("SYMBOLS", "EURUSD")
SYMBOLS = [s.strip() for s in SYMBOLS_RAW.split(",") if s.strip()]

TIMEFRAME_NAME = os.getenv("TIMEFRAME", "M1")
LOT = float(os.getenv("LOT", "0.01"))
STOP_LOSS_POINTS = int(os.getenv("STOP_LOSS_POINTS", "300"))
TAKE_PROFIT_POINTS = int(os.getenv("TAKE_PROFIT_POINTS", "600"))
DEVIATION = int(os.getenv("DEVIATION", "20"))
MAGIC = int(os.getenv("MAGIC", "123456"))
MAX_OPEN_POSITIONS_TOTAL = int(os.getenv("MAX_OPEN_POSITIONS_TOTAL", "2"))
MAX_OPEN_POSITIONS_PER_SYMBOL = int(os.getenv("MAX_OPEN_POSITIONS_PER_SYMBOL", "1"))
POLL_SECONDS = int(os.getenv("POLL_SECONDS", "5"))
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
    terminal = mt5.terminal_info()
    print("[INFO] Connected to MT5")
    print(f"[INFO] Account: {account.login if account else 'unknown'}")
    print(f"[INFO] Server: {account.server if account else 'unknown'}")
    print(f"[INFO] Terminal connected: {terminal.connected if terminal else 'unknown'}")
    print(f"[INFO] Watchlist: {SYMBOLS}")
    return True


def ensure_symbol(symbol: str):
    info = mt5.symbol_info(symbol)
    if info is None:
        print(f"[{symbol}] [ERROR] Symbol not found")
        return None

    if not info.visible:
        if not mt5.symbol_select(symbol, True):
            print(f"[{symbol}] [ERROR] symbol_select failed")
            return None

    return mt5.symbol_info(symbol)


def get_supported_filling_mode(symbol_info):
    preferred = [
        mt5.ORDER_FILLING_RETURN,
        mt5.ORDER_FILLING_IOC,
        mt5.ORDER_FILLING_FOK,
    ]

    filling_mode = getattr(symbol_info, "filling_mode", None)

    if filling_mode in preferred:
        return filling_mode

    return mt5.ORDER_FILLING_RETURN


def get_rates(symbol: str, timeframe, bars: int = 100):
    rates = mt5.copy_rates_from_pos(symbol, timeframe, 0, bars)
    if rates is None:
        return None
    return pd.DataFrame(rates)


def get_latest_bar_time(symbol: str, timeframe):
    df = get_rates(symbol, timeframe, 2)
    if df is None or df.empty:
        return None
    return int(df["time"].iloc[-1])


def analyze_signal(symbol: str, timeframe):
    df = get_rates(symbol, timeframe, 100)
    if df is None or len(df) < 30:
        return None, None, None

    df["fast_ma"] = df["close"].rolling(10).mean()
    df["slow_ma"] = df["close"].rolling(20).mean()

    prev_fast = df["fast_ma"].iloc[-2]
    prev_slow = df["slow_ma"].iloc[-2]
    curr_fast = df["fast_ma"].iloc[-1]
    curr_slow = df["slow_ma"].iloc[-1]

    signal = None

    if prev_fast <= prev_slow and curr_fast > curr_slow:
        signal = "buy"
    elif prev_fast >= prev_slow and curr_fast < curr_slow:
        signal = "sell"

    return signal, curr_fast, curr_slow


def get_positions(symbol: str = None):
    if symbol:
        positions = mt5.positions_get(symbol=symbol)
    else:
        positions = mt5.positions_get()
    return list(positions) if positions else []


def classify_positions(positions):
    buys = []
    sells = []

    for pos in positions:
        if pos.type == mt5.POSITION_TYPE_BUY:
            buys.append(pos)
        elif pos.type == mt5.POSITION_TYPE_SELL:
            sells.append(pos)

    return buys, sells


def close_position(position):
    symbol = position.symbol
    symbol_info = ensure_symbol(symbol)
    if symbol_info is None:
        return None

    filling_mode = get_supported_filling_mode(symbol_info)
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        print(f"[{symbol}] [ERROR] No tick for close")
        return None

    if position.type == mt5.POSITION_TYPE_BUY:
        order_type = mt5.ORDER_TYPE_SELL
        price = tick.bid
    else:
        order_type = mt5.ORDER_TYPE_BUY
        price = tick.ask

    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": symbol,
        "volume": position.volume,
        "type": order_type,
        "position": position.ticket,
        "price": price,
        "deviation": DEVIATION,
        "magic": MAGIC,
        "comment": "python-bot close",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": filling_mode,
    }

    check = mt5.order_check(request)
    print(f"[{symbol}] [INFO] close order_check: {check}")

    if DRY_RUN:
        print(f"[{symbol}] [DRY RUN] Close not sent for ticket {position.ticket}.")
        return check

    result = mt5.order_send(request)
    print(f"[{symbol}] [INFO] close result: {result}")
    return result


def open_trade(symbol: str, side: str):
    symbol_info = ensure_symbol(symbol)
    if symbol_info is None:
        return None

    filling_mode = get_supported_filling_mode(symbol_info)
    tick = mt5.symbol_info_tick(symbol)
    if tick is None:
        print(f"[{symbol}] [ERROR] No tick for open")
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
        print(f"[{symbol}] [ERROR] Invalid side")
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
        "type_filling": filling_mode,
    }

    check = mt5.order_check(request)
    print(f"[{symbol}] [INFO] open order_check: {check}")

    if DRY_RUN:
        print(f"[{symbol}] [DRY RUN] Open not sent for {side}.")
        return check

    result = mt5.order_send(request)
    print(f"[{symbol}] [INFO] open result: {result}")
    return result


def handle_signal(symbol: str, signal: str):
    total_positions = get_positions()
    symbol_positions = get_positions(symbol)
    buys, sells = classify_positions(symbol_positions)

    print(
        f"[{symbol}] [INFO] Position summary -> "
        f"symbol buys: {len(buys)}, symbol sells: {len(sells)}, total open: {len(total_positions)}"
    )

    if signal == "buy":
        if sells:
            print(f"[{symbol}] [INFO] Reverse signal to BUY. Closing sells first.")
            for pos in sells:
                close_position(pos)

        total_positions = get_positions()
        symbol_positions = get_positions(symbol)
        buys, sells = classify_positions(symbol_positions)

        if len(total_positions) >= MAX_OPEN_POSITIONS_TOTAL:
            print(f"[{symbol}] [INFO] Total max open positions reached. No new trade.")
            return

        if len(buys) >= MAX_OPEN_POSITIONS_PER_SYMBOL:
            print(f"[{symbol}] [INFO] BUY already open for symbol. No new trade.")
            return

        print(f"[{symbol}] [INFO] Opening BUY position.")
        open_trade(symbol, "buy")

    elif signal == "sell":
        if buys:
            print(f"[{symbol}] [INFO] Reverse signal to SELL. Closing buys first.")
            for pos in buys:
                close_position(pos)

        total_positions = get_positions()
        symbol_positions = get_positions(symbol)
        buys, sells = classify_positions(symbol_positions)

        if len(total_positions) >= MAX_OPEN_POSITIONS_TOTAL:
            print(f"[{symbol}] [INFO] Total max open positions reached. No new trade.")
            return

        if len(sells) >= MAX_OPEN_POSITIONS_PER_SYMBOL:
            print(f"[{symbol}] [INFO] SELL already open for symbol. No new trade.")
            return

        print(f"[{symbol}] [INFO] Opening SELL position.")
        open_trade(symbol, "sell")


def main():
    timeframe = TIMEFRAME_MAP.get(TIMEFRAME_NAME, mt5.TIMEFRAME_M1)

    if not connect():
        return

    last_bar_times = {}

    for symbol in SYMBOLS:
        info = ensure_symbol(symbol)
        if info is None:
            continue
        last_bar_times[symbol] = get_latest_bar_time(symbol, timeframe)

    print("[INFO] Bot initialized. Waiting for new candles on watchlist.")

    try:
        while True:
            for symbol in SYMBOLS:
                current_bar_time = get_latest_bar_time(symbol, timeframe)

                if current_bar_time is None:
                    print(f"[{symbol}] [WARN] Could not fetch latest bar time.")
                    continue

                previous_bar_time = last_bar_times.get(symbol)

                if previous_bar_time is None:
                    last_bar_times[symbol] = current_bar_time
                    print(f"[{symbol}] [INFO] Initialized candle tracking.")
                    continue

                if current_bar_time != previous_bar_time:
                    last_bar_times[symbol] = current_bar_time

                    signal, fast_ma, slow_ma = analyze_signal(symbol, timeframe)
                    symbol_positions = get_positions(symbol)

                    print(
                        f"[{symbol}] [INFO] New candle detected | "
                        f"Signal: {signal} | fast_ma: {fast_ma} | slow_ma: {slow_ma} | "
                        f"Open positions: {len(symbol_positions)}"
                    )

                    if signal in ("buy", "sell"):
                        handle_signal(symbol, signal)
                    else:
                        print(f"[{symbol}] [INFO] No crossover signal on this candle.")

            time.sleep(POLL_SECONDS)

    except KeyboardInterrupt:
        print("[INFO] Stopped by user")
    finally:
        mt5.shutdown()
        print("[INFO] MT5 connection closed")


if __name__ == "__main__":
    main()