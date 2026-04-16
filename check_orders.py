import os
from dhanhq import dhanhq

# --- SANDBOX CONFIG ---
# Replace with the exact Client ID and Access Token from your Sandbox PORTAL 
# (Make sure to leave IP address unrestricted in DhanHQ portal!)
CLIENT_ID = "2604143923" 
ACCESS_TOKEN = "eyJhbGciOiJIUzUxMiIsInR5cCI6IkpXVCJ9.eyJ0b2tlbkNvbnN1bWVyVHlwZSI6IlNFTEYiLCJwYXJ0bmVySWQiOiIiLCJkaGFuQ2xpZW50SWQiOiIyNjA0MTQzOTIzIiwid2ViaG9va1VybCI6IiIsImlzcyI6ImRoYW4iLCJleHAiOjE3Nzg3NDQ2ODl9.KJ2EQAJDz9cdModWhSD6Ux3MwTsRJcAy15bZHfZxmGq4xGXfazX3KGJDNCIBbxHk2xJ8gi1yquHpW6q8ZP73ag"

def main():
    print("Connecting to Dhan API...")
    dhan = dhanhq(CLIENT_ID, ACCESS_TOKEN)
    dhan.base_url = "https://sandbox.dhan.co/v2"
    
    print("\nfetching order list from Sandbox:")
    orders = dhan.get_order_list()
    
    if orders.get('status') == 'success':
        data = orders.get('data', [])
        if not data:
            print("You have no placed orders currently in your Sandbox.")
        else:
            print(f"Found {len(data)} Orders!")
            for order in data:
                print(f"[{order.get('orderStatus')}] {order.get('transactionType')} {order.get('tradingSymbol')} | Qty: {order.get('quantity')} | OrderId: {order.get('orderId')}")
    else:
        print("Failed to fetch orders!")
        print("Reason:", orders.get('remarks'))

if __name__ == "__main__":
    main()
