import pandas as pd
from flask import Flask, request, jsonify
from dhanhq import dhanhq, DhanContext

app = Flask(__name__)

# --- LIVE CONFIG ---
CLIENT_ID = "1100819221"
ACCESS_TOKEN = "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzUxMiJ9.eyJpc3MiOiJkaGFuIiwicGFydG5lcklkIjoiIiwiZXhwIjoxNzc3OTY1MzkyLCJpYXQiOjE3Nzc4Nzg5OTIsInRva2VuQ29uc3VtZXJUeXBlIjoiU0VMRiIsIndlYmhvb2tVcmwiOiIiLCJkaGFuQ2xpZW50SWQiOiIxMTAwODE5MjIxIn0.2KkCuMw93gRszOCTIqf1nHr3z6F7R6Zmss7iQRBuhINjESLlObRh8q6WqC6rAxKGp8NP4owMpXHUWEaG5J6hug"
SECRET_TOKEN = "JunnarTrader2026"

# Connect to the Live Trading System
dhan_context = DhanContext(client_id=CLIENT_ID, access_token=ACCESS_TOKEN)
dhan = dhanhq(dhan_context)

print("Syncing with Dhan Master Scrip List...")
SCRIP_URL = "https://images.dhan.co/api-data/api-scrip-master.csv"
df = pd.read_csv(SCRIP_URL, low_memory=False)

def get_security_id(symbol, price=0, option_type=None, manual_strike=None):
    try:
        symbol = symbol.upper()
        if "-ATM" in symbol or "-ITM" in symbol:
            base = symbol.split("-ATM")[0].split("-ITM")[0]
            if manual_strike:
                strike = float(manual_strike)
            else:
                if price == 0: return None, None
                step = 100 if "BANKNIFTY" in base else 50
                strike = round(price / step) * step
            
            # Extract multiplier if exists (e.g. BANKNIFTY-ITM2)
            mult = 1
            if "-ITM" in symbol:
                parts = symbol.split("-ITM")
                if len(parts) > 1 and parts[1].isdigit():
                    mult = int(parts[1])
            
            if option_type == 'CE':
                strike -= (step * mult)
            else:
                strike += (step * mult)
            
            print(f"🎯 Using {symbol} logic for {base}: Strike {strike}")
            
            match = df[
                (df['SEM_INSTRUMENT_NAME'] == 'OPTIDX') &
                (df['SEM_TRADING_SYMBOL'].str.contains(base)) &
                (df['SEM_STRIKE_PRICE'] == strike) &
                (df['SEM_OPTION_TYPE'] == option_type)
            ].sort_values(by='SEM_EXPIRY_DATE').head(1)
            
            if not match.empty:
                print(f"✅ Found: {match.iloc[0]['SEM_CUSTOM_SYMBOL']} | ID: {match.iloc[0]['SEM_SMST_SECURITY_ID']}")
                return match.iloc[0]['SEM_SMST_SECURITY_ID'], match.iloc[0]['SEM_INSTRUMENT_NAME']
        return None, None
    except Exception as e:
        print(f"❌ Lookup Error: {e}")
        return None, None

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.get_json()
        print(f"📥 Received Webhook Data: {data}")

        if data.get('secret') != SECRET_TOKEN:
            return jsonify({"error": "Unauthorized"}), 401

        symbol_raw = data.get('symbol')
        if not symbol_raw:
            print("⚠️ Missing symbol. Ignoring.")
            return jsonify({"error": "Missing symbol"}), 400
            
        symbol = symbol_raw.upper()
        price = float(data.get('price', 0))
        option_type = data.get('option_type', 'CE').upper()
        manual_strike = data.get('itm_strike')

        sec_id, inst_name = get_security_id(symbol, price, option_type, manual_strike)
        
        if not sec_id:
            return jsonify({"status": "error", "remarks": "Symbol not found"}), 400

        exch_seg = dhan.NSE_FNO if inst_name in ['OPTIDX', 'OPTSTK', 'FUTIDX', 'FUTSTK'] else dhan.NSE
        order_type_str = data.get('order_type', 'MARKET').upper()
        side_str = data.get('side', 'BUY').upper()
        
        final_price = 0.0
        dhan_order_type = dhan.MARKET

        if order_type_str == 'MARKET':
            print(f"🔍 Fetching Precise LTP for {sec_id}...")
            seg_key = "NSE_FNO" if exch_seg == dhan.NSE_FNO else "NSE"
            securities = {seg_key: [int(sec_id)]}
            quote = dhan.ticker_data(securities)
            try:
                final_price = float(quote['data']['data'][seg_key][str(sec_id)]['last_price'])
                print(f"🎯 LTP Found: {final_price}. Placing Precise LIMIT order.")
                dhan_order_type = dhan.LIMIT
            except:
                print("⚠️ LTP Fetch failed, falling back to MARKET order")
        else:
            dhan_order_type = dhan.LIMIT
            final_price = float(data.get('price', 0))

        response = dhan.place_order(
            security_id=int(sec_id),
            exchange_segment=exch_seg,
            transaction_type=dhan.BUY if side_str == 'BUY' else dhan.SELL,
            quantity=int(data.get('quantity', 0)),
            order_type=dhan_order_type,
            product_type=dhan.INTRA,
            price=float(final_price),
            after_market_order=False 
        )
        
        print(f"📡 Dhan API Order Response: {response}")
        return jsonify(response), 200

    except Exception as e:
        import traceback
        print(f"❌ Order Execution Error:\n{traceback.format_exc()}")
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=80)
