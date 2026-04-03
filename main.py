import logging
import os
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

# Try to import tonutils safely - updated for latest 2.x
try:
    from tonutils.client import ToncenterV3Client
    from tonutils.dns import DNS
    from tonutils.utils.address import Address
    from tonutils.wallet import WalletV4R2
    LIBRARY_OK = True
except Exception as import_error:
    LIBRARY_OK = False
    logging.error(f"tonutils import failed: {import_error}")

load_dotenv()

# Config
IS_TESTNET = os.getenv("IS_TESTNET", "false").lower() == "true"
TONCENTER_API_KEY = os.getenv("TONCENTER_API_KEY")

if not TONCENTER_API_KEY:
    raise RuntimeError("TONCENTER_API_KEY environment variable is missing! Add it in Railway Variables.")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

app = FastAPI(title="My TON API (Pixy Style)")

# ================= MODELS =================
class ApiResponse(BaseModel):
    ok: bool
    message: str
    error: Optional[str] = None
    order_id: Optional[str] = None
    tx_hash: Optional[str] = None
    recipient: Optional[str] = None

class TonBuyRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=32)
    amount: float = Field(..., gt=0)
    seed: str = Field(...)
    order_id: Optional[str] = None

class TonTransferRequest(BaseModel):
    to_address: str = Field(...)
    amount: float = Field(..., gt=0)
    seed: str = Field(...)
    comment: Optional[str] = None
    order_id: Optional[str] = None

# ================= ROUTES =================
@app.get("/health")
async def health():
    return {
        "ok": LIBRARY_OK,
        "status": "running" if LIBRARY_OK else "library_error",
        "testnet": IS_TESTNET,
        "library_loaded": LIBRARY_OK
    }

@app.get("/")
async def root():
    return {"message": "TON API is live. Use /ton/buy or /ton/transfer. Check /health first."}

if not LIBRARY_OK:
    @app.post("/ton/buy")
    @app.post("/ton/transfer")
    async def library_error():
        raise HTTPException(
            status_code=500,
            detail="tonutils library failed to load on Railway. Redeploy after updating requirements.txt"
        )

# Only register full endpoints if library loaded
if LIBRARY_OK:
    # Initialize client once
    client = ToncenterV3Client(
        api_key=TONCENTER_API_KEY,
        is_testnet=IS_TESTNET,
        rps=5,
        max_retries=3
    )

    async def get_wallet_from_seed(seed: str):
        words = seed.strip().split()
        if len(words) not in (12, 24):
            raise ValueError("INVALID_MNEMONIC: Seed must be 12 or 24 words")
        try:
            # v2.x requires this signature: wallet, pubk, privk, mnemonic
            wallet, _, _, _ = WalletV4R2.from_mnemonic(client, words)
            return wallet
        except Exception as e:
            err = str(e).lower()
            if "mnemonic" in err or "invalid" in err:
                raise ValueError("INVALID_MNEMONIC: Seed phrase is invalid")
            raise ValueError(f"WALLET_CREATION_FAILED: {str(e)}")

    async def resolve_username_to_address(username: str) -> str:
        domain = f"{username.lower().strip()}.t.me"
        dns = DNS(client)
        try:
            # DNS resolve in 2.x returns object with .wallet_address
            resolved = await dns.resolve(domain)
            if resolved and resolved.wallet_address:
                addr = Address(resolved.wallet_address)
                return addr.to_str(is_user_friendly=True)
            raise ValueError("NO_DNS_RECORD")
        except Exception as e:
            logger.error(f"DNS Resolve error: {e}")
            raise ValueError(f"CANNOT_RESOLVE: @{username} has no .t.me DNS record (only Fragment NFT usernames usually work)")

    @app.post("/ton/buy", response_model=ApiResponse)
    async def ton_buy(req: TonBuyRequest):
        try:
            logger.info(f"Buy request → @{req.username} | {req.amount} TON")
            wallet = await get_wallet_from_seed(req.seed)

            # Check if address derived correctly
            # logger.info(f"Wallet address: {wallet.address.to_str()}")

            # Balance check (v2.x method)
            balance_nano = await wallet.get_balance()
            balance = balance_nano / 1_000_000_000
            if balance < req.amount + 0.05:
                raise ValueError(f"INSUFFICIENT_FUNDS: Need {req.amount + 0.05:.3f} TON")

            recipient = await resolve_username_to_address(req.username)

            comment = f"Topup via API | Order: {req.order_id or 'N/A'}"
            # Transfer method (v2.x)
            tx_hash = await wallet.transfer(
                destination=recipient,
                amount=req.amount,
                comment=comment
            )

            return ApiResponse(
                ok=True,
                message=f"Successfully sent {req.amount} TON to @{req.username}",
                tx_hash=tx_hash,
                recipient=recipient,
                order_id=req.order_id
            )
        except ValueError as ve:
            return ApiResponse(ok=False, message=str(ve), error="VALIDATION_ERROR", order_id=req.order_id)
        except Exception as e:
            logger.error(f"Buy error: {e}")
            return ApiResponse(ok=False, message=str(e), error="INTERNAL_ERROR")

    @app.post("/ton/transfer", response_model=ApiResponse)
    async def ton_transfer(req: TonTransferRequest):
        try:
            logger.info(f"Transfer request → {req.to_address} | {req.amount} TON")
            wallet = await get_wallet_from_seed(req.seed)

            balance_nano = await wallet.get_balance()
            balance = balance_nano / 1_000_000_000
            if balance < req.amount + 0.05:
                raise ValueError(f"INSUFFICIENT_FUNDS: Need {req.amount + 0.05:.3f} TON")

            try:
                Address(req.to_address)
            except Exception:
                raise ValueError("INVALID_ADDRESS: Wrong TON address format")

            comment = req.comment or f"Transfer via API | Order: {req.order_id or 'N/A'}"
            tx_hash = await wallet.transfer(
                destination=req.to_address,
                amount=req.amount,
                comment=comment
            )

            return ApiResponse(
                ok=True,
                message=f"Successfully sent {req.amount} TON",
                tx_hash=tx_hash,
                recipient=req.to_address,
                order_id=req.order_id
            )
        except ValueError as ve:
            return ApiResponse(ok=False, message=str(ve), error="VALIDATION_ERROR", order_id=req.order_id)
        except Exception as e:
            logger.error(f"Transfer error: {e}")
            return ApiResponse(ok=False, message=str(e), error="INTERNAL_ERROR")

# Run command (for local testing)
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
