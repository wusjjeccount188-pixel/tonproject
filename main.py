import logging
import os
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

# Try to import tonutils safely
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
        "ok": True,
        "status": "running",
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
            wallet = WalletV4R2.from_mnemonic(client, words)
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
            resolved = await dns.resolve(domain)
            if resolved and resolved.address:
                addr = Address(resolved.address)
                return addr.to_str(is_user_friendly=True)
            raise ValueError("NO_DNS_RECORD")
        except Exception:
            raise ValueError(f"CANNOT_RESOLVE: @{username} has no .t.me DNS record (only Fragment NFT usernames usually work)")

    @app.post("/ton/buy", response_model=ApiResponse)
    async def ton_buy(req: TonBuyRequest):
        try:
            logger.info(f"Buy request → @{req.username} | {req.amount} TON")
            wallet = await get_wallet_from_seed(req.seed)

            # Rough balance check (optional but recommended)
            balance_nano = await wallet.get_balance()
            balance = balance_nano / 1_000_000_000
            if balance < req.amount + 0.05:
                raise ValueError(f"INSUFFICIENT_FUNDS: You need at least {req.amount + 0.05:.3f} TON")

            recipient = await resolve_username_to_address(req.username)

            comment = f"Topup via API | Order: {req.order_id or 'N/A'}"
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
            code = str(ve).split(":")[0] if ":" in str(ve) else "ERROR"
            return ApiResponse(ok=False, message=str(ve), error=code, order_id=req.order_id)
        except Exception as e:
            logger.error(f"Buy error: {e}")
            return ApiResponse(ok=False, message="INTERNAL_ERROR", error="INTERNAL_ERROR")

    @app.post("/ton/transfer", response_model=ApiResponse)
    async def ton_transfer(req: TonTransferRequest):
        try:
            logger.info(f"Transfer request → {req.to_address} | {req.amount} TON")
            wallet = await get_wallet_from_seed(req.seed)

            balance_nano = await wallet.get_balance()
            balance = balance_nano / 1_000_000_000
            if balance < req.amount + 0.05:
                raise ValueError(f"INSUFFICIENT_FUNDS: You need at least {req.amount + 0.05:.3f} TON")

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
            code = str(ve).split(":")[0] if ":" in str(ve) else "ERROR"
            return ApiResponse(ok=False, message=str(ve), error=code, order_id=req.order_id)
        except Exception as e:
            logger.error(f"Transfer error: {e}")
            return ApiResponse(ok=False, message="INTERNAL_ERROR", error="INTERNAL_ERROR")

# Run command (for local testing)
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
