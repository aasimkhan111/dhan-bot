
import pandas as pd

# Mocking the base structure of the logic used in live_bot.py
def test_itm_logic(symbol, price, option_type):
    symbol = symbol.upper()
    if "-ATM" in symbol or "-ITM" in symbol:
        base = symbol.split("-ATM")[0].split("-ITM")[0]
        step = 100 if "BANKNIFTY" in base else 50
        strike = round(price / step) * step
        
        if "-ITM" in symbol:
            if option_type == 'CE':
                strike -= step
            else: 
                strike += step
            print(f"Calculating ITM for {base}: Price {price} -> Strike {strike}")
        else:
            print(f"Calculating ATM for {base}: Price {price} -> Strike {strike}")
    return strike

print("--- BANKNIFTY TESTS ---")
# Price 56135 -> ATM 56100
assert test_itm_logic("BANKNIFTY-ATM", 56135, "CE") == 56100
# Price 56135 -> ITM CE -> 56100 - 100 = 56000
assert test_itm_logic("BANKNIFTY-ITM", 56135, "CE") == 56000
# Price 56135 -> ITM PE -> 56100 + 100 = 56200
assert test_itm_logic("BANKNIFTY-ITM", 56135, "PE") == 56200

print("\n--- NIFTY TESTS ---")
# Price 24135 -> ATM 24150
assert test_itm_logic("NIFTY-ATM", 24135, "CE") == 24150
# Price 24135 -> ITM CE -> 24150 - 50 = 24100
assert test_itm_logic("NIFTY-ITM", 24135, "CE") == 24100

print("\n✅ All Logic Tests Passed!")
