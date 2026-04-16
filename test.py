import pandas as pd
from dhanhq import dhanhq

CLIENT_ID = "2604143923"
ACCESS_TOKEN = "eyJhbGciOiJIUzUxMiIsInR5cCI6IkpXVCJ9.eyJ0b2tlbkNvbnN1bWVyVHlwZSI6IlNFTEYiLCJwYXJ0bmVySWQiOiIiLCJkaGFuQ2xpZW50SWQiOiIyNjA0MTQzOTIzIiwid2ViaG9va1VybCI6IiIsImlzcyI6ImRoYW4iLCJleHAiOjE3Nzg3NDQ2ODl9.KJ2EQAJDz9cdModWhSD6Ux3MwTsRJcAy15bZHfZxmGq4xGXfazX3KGJDNCIBbxHk2xJ8gi1yquHpW6q8ZP73ag"
dhan = dhanhq(CLIENT_ID, ACCESS_TOKEN)
dhan.base_url = "https://sandbox.dhan.co/v2"

sec_id = "1333" # HDFCBANK

response = dhan.place_order(
    security_id=sec_id,
    exchange_segment=dhan.NSE,
    transaction_type=dhan.BUY,
    quantity=1,
    order_type=dhan.MARKET,
    product_type=dhan.INTRA,
    price=0,
    after_market_order=False 
)
print("Response:", response)
