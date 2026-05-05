import pandas as pd
from flask import Flask, request, jsonify
from dhanhq import dhanhq, DhanContext

app = Flask(__name__)

# --- LIVE CONFIG ---
CLIENT_ID = "1100819221"
ACCESS_TOKEN = "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzUxMiJ9.eyJpc3MiOiJkaGFuIiwicGFydG5lcklkIjoiIiwiZXhwIjoxNzc4MDQwMDUzLCJpYXQiOjE3Nzc5NTM2NTMsInRva2VuQ29uc3VtZXJUeXBlIjoiU0VMRiIsIndlYmhvb2tVcmwiOiIiLCJkaGFuQ2xpZW50SWQiOiIxMTAwODE5MjIxIn0.G7AewK4Ex-XsNwIucC7aoivUbNVqmZNOvC_RmUIxYp9QjIEQzyWDRyOAt-LWiIFIr1nYi6IhRB4j1Jo_3hPjjw"
SECRET_TOKEN = "JunnarTrader2026"

# Connect to Dhan
dhan_context = DhanContext(client_id=CLIENT_ID, access_token=ACCESS_TOKEN)
dhan = dhanhq(dhan_context)

print("Syncing with Dhan Master Scrip List...")
SCRIP_URL = "https://images.dhan.co/api-data/api-scrip-master.csv"
df = pd.read_csv(SCRIP_URL, low_memory=False)
print(f"✅ Loaded {len(df)} scrips from Dhan.")

def get_security_id(symbol, price=0, opt_type='CE'):
    try:
        symbol = symbol.upper()
        opt_type = opt_type.upper()
        
        print(f"🔍 Searching for: Symbol={symbol}, Price={price}, Type={opt_type}")
        
        if "-ATM" in symbol or "-ITM" in symbol:
            # Detect Base
            base = "BANKNIFTY" if "BANKNIFTY" in symbol else "NIFTY"
            step = 100 if base == "BANKNIFTY" else 50
            strike = int(round(price / step) * step)
            
            print(f"⚙️ Calc: Base={base}, Step={step}, Target Strike={strike}")
            
            # Step 1: Filter by Instrument and Base name
            mask = (df['SEM_INSTRUMENT_NAME'] == 'OPTIDX') & \
                   (df['SEM_TRADING_SYMBOL'].str.contains(base, case=False))
            
            # Step 2: Filter by Strike Price (handling float precision)
            mask &= (df['SEM_STRIKE_PRICE'].astype(float).astype(int) == strike)
            
            # Step 3: Filter by Option Type (Check CE/PE and CALL/PUT)
            opt_types_to_check = [opt_type]
            if opt_type == 'CE': opt_types_to_check.append('CALL')
            if opt_type == 'PE': opt_types_to_check.append('PUT')
            
            final_mask = mask & (df['SEM_OPTION_TYPE'].isin(opt_types_to_check))
            
            res = df[final_mask].sort_values(by='SEM_EXPIRY_DATE')
            
            if not res.empty:
                found = res.iloc[0]
                print(f"✅ Found Match: {found['SEM_TRADING_SYMBOL']} | ID: {found['SEM_SMART_SYMBOL']}")
                return found['SEM_SMART_SYMBOL'], found['SEM_EXCHANGE_SEGMENT']
            else:
                print(f"❌ No match found in Master List for Strike {strike} and Type {opt_type}")
        
        # Direct lookup if not ATM/ITM
        res = df[df['SEM_TRADING_SYMBOL'] == symbol]
        if not res.empty:
            return res.iloc[0]['SEM_SMART_SYMBOL'], res.iloc[0]['SEM_EXCHANGE_SEGMENT']
            
        return None, None
    except Exception as e:
        print(f"⚠️ Lookup Error: {str(e)}")
        return None, None

@app.route('/webhook', methods=['POST'])
def webhook():
    try:
        data = request.get_json(force=True, silent=True)
        if not data:
            return jsonify({"error": "Invalid JSON"}), 400

        print(f"\n📥 Received Signal: {data}")

        if data.get('secret') != SECRET_TOKEN:
            return jsonify({"error": "Invalid Secret"}), 403

        # --- EXIT LOGIC ---
        if data.get('action') == 'exit':
            print("🛑 Processing Square Off...")
            pos_res = dhan.get_positions()
            if pos_res.get('status') == 'success':
                positions = pos_res.get('data', [])
                for pos in positions:
                    qty = int(pos.get('netQty', 0))
                    if qty != 0:
                        print(f"Closing {pos.get('tradingSymbol')} | Qty: {qty}")
                        dhan.place_order(
                            security_id=pos.get('securityId'),
                            exchange_segment=pos.get('exchangeSegment'),
                            transaction_type=dhan.SELL if qty > 0 else dhan.BUY,
                            quantity=abs(qty),
                            order_type=dhan.MARKET,
                            product_type=pos.get('productType'),
                            after_market_order=False
                        )
                return jsonify({"status": "success", "message": "Exit Done"}), 200
            return jsonify({"error": "Fetch Positions Failed"}), 500

        # --- ORDER LOGIC ---
        symbol = data.get('symbol')
        if not symbol:
            return jsonify({"error": "Missing Symbol"}), 400
            
        side = data.get('side', 'buy').upper()
        price = float(data.get('price', 0))
        opt_type = data.get('option_type', 'CE').upper()

        sec_id, exch_seg = get_security_id(symbol, price, opt_type)
        
        if not sec_id:
            return jsonify({"error": f"Symbol {symbol} not found in Dhan Master List"}), 404

        order_res = dhan.place_order(
            security_id=int(sec_id),
            exchange_segment=exch_seg,
            transaction_type=dhan.BUY if side == 'BUY' else dhan.SELL,
            quantity=int(data.get('quantity', 0)),
            order_type=dhan.MARKET,
            product_type=dhan.INTRA,
            after_market_order=False
        )
        
        print(f"📡 Dhan Response: {order_res}")
        return jsonify(order_res), 200

    except Exception as e:
        print(f"❌ Webhook Error: {str(e)}")
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=80)
