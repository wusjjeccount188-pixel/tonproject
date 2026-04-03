import logging
import os
import asyncio
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

# Updated imports for tonutils v2.x (Client is now at the root or specific provider)
try:
    from tonutils.client import ToncenterClient
    from tonutils.utils import Address
    from tonutils.wallet import WalletV4R2
    from tonutils.net import DNS
    LIBRARY_OK = True
except Exception as import_error:
    LIBRARY_OK = False
    logging.error(f"tonutils import failed: {import_error}")

load_dotenv()

# Config
IS_TESTNET = os.getenv("IS_TESTNET", "false").lower() == "true"
TONCENTER_API_KEY = os.getenv("TONCENTER_API_KEY")

if not TONCENTER_API_KEY:
    # We raise this to stop the build if the key is missing in Railway Variables
    logging.warning("TONCENTER_API_KEY is missing! The API will not function.")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

app = FastAPI(title="My TON API (v2 Fixed)")

# ================= MODELS =================
class ApiResponse(BaseModel):
    ok: bool
    message: str
    error: Optional[str] = None
    tx_hash: Optional[str] = None
    recipient: Optional[str] = None

class TonBuyRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=32)
    amount: float = Field(..., gt=0)
    seed: str = Field(...)

class TonTransferRequest(BaseModel):
    to_address: str = Field(...)
    amount: float = Field(..., gt=0)
    seed: str = Field(...)
    comment: Optional[str] = None

# ================= ROUTES =================
@app.get("/health")
async def health():
    return {
        "ok": LIBRARY_OK,
        "library_loaded": LIBRARY_OK,
        "testnet": IS_TESTNET,
        "api_key_set": bool(TONCENTER_API_KEY)
    }

if not LIBRARY_OK:
    @app.post("/ton/buy")
    @app.post("/ton/transfer")
    async def library_error():
        raise HTTPException(
            status_code=500,
            detail="Library Import Error: Ensure tonutils>=2.0.0 is in requirements.txt and 'Clear Build Cache' on Railway."
        )

if LIBRARY_OK:
    # Initialize client (v2.x uses 'key' parameter)
    client = ToncenterClient(
        key=TONCENTER_API_KEY,
        is_testnet=IS_TESTNET
    )

    async def get_wallet(seed: str):
        mnemonic = seed.strip().split()
        # v2.x WalletV4R2.from_mnemonic returns (wallet, public_key, private_key, mnemonic)
        wallet, _, _, _ = WalletV4R2.from_mnemonic(client, mnemonic)
        return wallet

    @app.post("/ton/buy", response_model=ApiResponse)
    async def ton_buy(req: TonBuyRequest):
        try:
            wallet = await get_wallet(req.seed)
            
            # Resolve .t.me DNS
            dns = DNS(client)
            resolved = await dns.resolve(f"{req.username.lower()}.t.me")
            if not resolved or not resolved.wallet_address:
                return ApiResponse(ok=False, message=f"Could not resolve @{req.username}", error="DNS_FAILED")
            
            recipient = Address(resolved.wallet_address).to_str(is_user_friendly=True)
            
            # Transfer
            tx_hash = await wallet.transfer(
                destination=recipient,
                amount=req.amount,
                comment=f"Buy for @{req.username}"
            )
            
            return ApiResponse(ok=True, message="Transfer successful", tx_hash=tx_hash, recipient=recipient)
        except Exception as e:
            logger.error(f"Buy Error: {e}")
            return ApiResponse(ok=False, message=str(e), error="INTERNAL_ERROR")

    @app.post("/ton/transfer", response_model=ApiResponse)
    async def ton_transfer(req: TonTransferRequest):
        try:
            wallet = await get_wallet(req.seed)
            
            tx_hash = await wallet.transfer(
                destination=req.to_address,
                amount=req.amount,
                comment=req.comment or "API Transfer"
            )
            
            return ApiResponse(ok=True, message="Sent successfully", tx_hash=tx_hash, recipient=req.to_address)
        except Exception as e:
            logger.error(f"Transfer Error: {e}")
            return ApiResponse(ok=False, message=str(e), error="INTERNAL_ERROR")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
