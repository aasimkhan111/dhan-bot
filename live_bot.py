import pandas as pd
from flask import Flask, request, jsonify
from dhanhq import dhanhq

app = Flask(__name__)

# --- LIVE CONFIG ---
# Replace these with your exact Client ID and Access Token from your main LIVE PORTAL!
CLIENT_ID = "1100819221"
ACCESS_TOKEN = "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzUxMiJ9.eyJpc3MiOiJkaGFuIiwicGFydG5lcklkIjoiIiwiZXhwIjoxNzc2ODM3NTI5LCJpYXQiOjE3NzY3NTExMjksInRva2VuQ29uc3VtZXJUeXBlIjoiU0VMRiIsIndlYmhvb2tVcmwiOiIiLCJkaGFuQ2xpZW50SWQiOiIxMTAwODE5MjIxIn0.HWMKsxwwVYd9PTV_cQfjR0fvuV1wpN_z7hfYEQLA4xt5cB22VPUCUlguw-JNulPjXihGzBc_SkrWLJCaZUO2xg"
SECRET_TOKEN = "JunnarTrader2026"

# Connect to the Live Trading System
dhan = dhanhq(CLIENT_ID, ACCESS_TOKEN)

# Notice here that we REMOVED the `dhan.base_url = ...` line!
# The default library URL points directly to the live environment.

print("Syncing with Dhan Master Scrip List...")
SCRIP_URL = "https://images.dhan.co/api-data/api-scrip-master.csv"

# Added low_memory=False to fix the DtypeWarning
df = pd.read_csv(SCRIP_URL, low_memory=False)

def get_security_id(symbol, price=0, option_type=None, manual_strike=None):
    try:
        symbol = symbol.upper()
        # --- DYNAMIC ATM/ITM LOGIC ---
        if "-ATM" in symbol or "-ITM" in symbol:
            base = symbol.split("-ATM")[0].split("-ITM")[0]
            
            # Use manual strike if provided (for precise exits), else calculate from price
            if manual_strike:
                strike = float(manual_strike)
            else:
                if price == 0:
                    print(f"❌ Error: price=0 received for {symbol} request.")
                    return None, None
                # Calculate Strike (Step of 100 for BankNifty, 50 for Nifty)
                step = 100 if "BANKNIFTY" in base else 50
                strike = round(price / step) * step
                
                # Apply ITM Offset (100 points for BankNifty, 50 for Nifty)
                if "-ITM" in symbol:
                    if option_type == 'CE': strike -= step
                    else: strike += step

            print(f"🎯 Using {symbol} logic for {base}: Strike {strike}")
            
            # Find the option with this strike and nearest expiry
            match = df[(df['SEM_INSTRUMENT_NAME'] == 'OPTIDX') & 
                       (df['SEM_STRIKE_PRICE'].astype(float) == float(strike)) &
                       (df['SEM_OPTION_TYPE'] == option_type) &
                       ((df['SM_SYMBOL_NAME'].str.contains(base, case=False, na=False)) | 
                        (df['SEM_CUSTOM_SYMBOL'].str.contains(base, case=False, na=False)) |
                        (df['SEM_TRADING_SYMBOL'].str.contains(base, case=False, na=False)))]
            
            if match.empty:
                print(f"⚠️ No strike {strike} found for {base}.")
                return None, None
            
        elif symbol.endswith('-I'):
            base = symbol.replace('-I', '')
            match = df[(df['SEM_INSTRUMENT_NAME'].isin(['FUTIDX', 'FUTSTK'])) & 
                       (df['SEM_TRADING_SYMBOL'].str.startswith(base)) &
                       (df['SEM_EXM_EXCH_ID'] == 'NSE')]
        else:
            # Regular exact match
            match = df[(df['SEM_TRADING_SYMBOL'].str.upper() == symbol) & (df['SEM_EXM_EXCH_ID'].isin(['NSE', 'NFO']))]
            if match.empty:
                match = df[(df['SEM_CUSTOM_SYMBOL'].str.upper() == symbol) & (df['SEM_EXM_EXCH_ID'].isin(['NSE', 'NFO']))]

        if not match.empty:
            # Sort by expiry to get the nearest one
            if 'SEM_EXPIRY_DATE' in match.columns:
                match = match.sort_values('SEM_EXPIRY_DATE')
            
            sec_id = str(match.iloc[0]['SEM_SMST_SECURITY_ID'])
            inst_name = str(match.iloc[0]['SEM_INSTRUMENT_NAME'])
            final_symbol = str(match.iloc[0]['SEM_TRADING_SYMBOL'])
            print(f"✅ Found: {final_symbol} | ID: {sec_id}")
            return sec_id, inst_name
        else:
            print(f"❌ Symbol {symbol} not found.")
            return None, None
    except Exception as e:
        print(f"Lookup Error: {e}")
        return None, None

@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.get_json(force=True, silent=True)
    
    if not data or data.get('secret') != SECRET_TOKEN:
        print(f"🔴 403 ERROR! Unauthorized request.")
        return jsonify({"error": "Unauthorized"}), 403

    symbol = data.get('symbol')
    price = float(data.get('price', 0))
    opt_type = data.get('option_type', 'CE')
    manual_strike = data.get('itm_strike')
    
    sec_id, inst_name = get_security_id(symbol, price, opt_type, manual_strike)

    if not sec_id:
        return jsonify({"error": f"Symbol {symbol} not found"}), 400

    # Auto-detect segment
    # Using library constants for maximum compatibility
    exch_seg = dhan.NSE_FNO if inst_name in ['OPTIDX', 'OPTSTK', 'FUTIDX', 'FUTSTK'] else dhan.NSE

    # Handle Order Type and Price logic - FINAL STABLE VERSION
    order_type_str = data.get('order_type', 'MARKET').upper()
    side_str = data.get('side', 'BUY').upper()
    transaction_type = dhan.BUY if side_str == 'BUY' else dhan.SELL
    dhan_product_type = dhan.INTRA
    
    try:
        if order_type_str == 'MARKET':
            dhan_order_type = dhan.MARKET
            final_price = 0.0
            print(f"🔄 PLACING MARKET ORDER for {sec_id}")
        else:
            dhan_order_type = dhan.LIMIT
            final_price = float(data.get('price', 0))

        # Place order
        print(f"🚀 ATTEMPTING {side_str} ({order_type_str}) for {sec_id} | Qty: {data.get('quantity')}")
        
        response = dhan.place_order(
            security_id=sec_id,
            exchange_segment=exch_seg,
            transaction_type=transaction_type,
            quantity=int(data.get('quantity', 0)),
            order_type=dhan_order_type,
            product_type=dhan_product_type,
            price=final_price,
            after_market_order=False 
        )
        
        print(f"📡 Dhan API Order Response: {response}")
        return jsonify(response), 200

    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        print(f"❌ Order Execution Error:\n{error_details}")
        return jsonify({"error": str(e), "details": error_details}), 500

if __name__ == '__main__':
    # Listen on all public IPs on port 80 (standard HTTP port)
    app.run(host='0.0.0.0', port=80)
