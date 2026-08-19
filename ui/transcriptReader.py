import os
from typing import Optional, Tuple
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
import requests
import streamlit as st
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="Firefighter Dispatch Parser API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 開發階段允許所有來源，生產環境可指定網站網址
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def _secret(name):
    """Read a deployment secret from the environment or Streamlit secrets."""
    value = os.getenv(name)
    if value:
        return value
    try:
        return st.secrets.get(name)
    except Exception:
        return None


OPENAI_API_KEY = _secret("OPENAI_API_KEY")
app = FastAPI(title="Firefighter Dispatch Parser API")

# 1. 結構化資料模型
class ParsedMessage(BaseModel):
    message_type: str = Field(
        default="chatter",
        description=(
            "One of initial_request, demand_update, hydrant_failure, "
            "failure_and_demand, or chatter"
        ),
    )
    latitude: Optional[float] = Field(
        default=None, 
        description="Latitude if coordinates are explicitly provided"
    )
    longitude: Optional[float] = Field(
        default=None, 
        description="Longitude if coordinates are explicitly provided"
    )
    location_name: Optional[str] = Field(
        default=None, 
        description="Landmark, building name, or address if exact coordinates are missing"
    )
    water_lpm: float = Field(
        default=0.0, 
        description="Water requirement in Liters per minute (L/min). "
            "If a number for water is provided without any unit (e.g. 'need 4000'), assume it is already in L/min. "
            "If given in Gallons/min, convert it (1 US Gallon ≈ 3.78541 Liters). "
            "If no water requirement is mentioned at all, set to 0."
    )
    hydrant_id: Optional[str] = Field(
        default=None,
        description="Normalized hydrant ID such as H0479, if explicitly mentioned",
    )
    out_of_service: bool = Field(
        default=False,
        description="True only when a hydrant is explicitly reported unavailable",
    )
    clarification_needed: bool = Field(
        default=False,
        description="True when the message is too ambiguous to safely change operational state",
    )
    clarification: Optional[str] = Field(
        default=None,
        description="Short question to resolve an ambiguous operational message",
    )

class APIResponse(BaseModel):
    x: Optional[float] = Field(None, description="Latitude")
    y: Optional[float] = Field(None, description="Longitude")
    w: float = Field(..., description="Water requirement in L/min")
    provider: str = Field(..., description="LLM provider used")
    message_type: str = Field(..., description="Interpreted dispatch message type")
    hydrant_id: Optional[str] = Field(None, description="Normalized hydrant ID")
    out_of_service: bool = Field(False, description="Whether the hydrant is unavailable")
    clarification_needed: bool = Field(False, description="Whether clarification is needed")
    clarification: Optional[str] = Field(None, description="Clarification question")

# 2. 地理編碼函式
def geocode_location(query: str) -> Tuple[float, float]:
    url = "https://nominatim.openstreetmap.org/search"
    headers = {"User-Agent": "FirefighterDispatchApp/1.0"}
    params = {
        "q": query, 
        "format": "json", 
        "limit": 1
    }
    
    resp = requests.get(url, params=params, headers=headers)
    data = resp.json()
    if not data:
        raise ValueError(f"Could not geocode location: {query}")
    return float(data[0]["lat"]), float(data[0]["lon"])

# 3. 系統提示詞（特別強化錯字自動校正與在地縮寫轉換）
SYSTEM_PROMPT = (
    "You are an assistant for a fire department dispatch system in the Copenhagen / Denmark metropolitan area.\n"
    "Your job is to interpret noisy, informal, typo-filled fire-dispatch radio messages and extract exact coordinates (or landmark name), water requirement, and operational updates.\n"
    "Classify each message as exactly one of: initial_request, demand_update, hydrant_failure, failure_and_demand, chatter.\n"
    "Use the intended meaning, not exact wording. Correct ordinary speech-recognition errors, spelling mistakes, missing punctuation, abbreviations, and minor word-order problems.\n"
    "Examples of failure messages include: 'H0479 is out of service', 'hydrant 0479 has gone bad', 'we lost hydrant 479', '479 is dead', 'H 0479 not working', 'hydrant four seven nine is unavailable', and misspellings such as 'out of servce' or 'unavailble'. Normalize every supported form to 'H0479'.\n"
    "A demand_update is an absolute new total. Accept forms such as 'increase demand to 5000 L/min', 'raise the water requirement to five thousand', 'we now need 5000', 'make that 5000 litres per minute', and 'bump the requirement up to 5000'. Do NOT treat 'increase demand by 5000' or 'add another 5000' as an absolute update.\n"
    "Restoration messages such as 'back in service', 'repaired', or 'available again' are not supported and must be chatter.\n"
    "Update messages may omit location because the caller supplies the active incident location. Never invent missing coordinates, hydrant IDs, or demand. If a state-changing message is ambiguous about the hydrant or absolute demand, set clarification_needed=true, provide a short clarification question, and do not create an operational action.\n\n"
    "Location Parsing & Typo Resolution Rules:\n"
    "1. If explicit coordinates (lat, lon) are given, use them.\n"
    "2. If landmarks, stations, streets, or squares are mentioned, ALWAYS normalize and correct typos and abbreviations into their full official Danish or English names, appending city context (e.g. ', Copenhagen') if helpful for geocoding.\n"
    "   - Typo examples: 'Kogens nytory' / 'Kongens Nytorf' -> 'Kongens Nytorv, Copenhagen'\n"
    "   - Abbreviation examples: 'KBH H' / 'Central Station' -> 'København H, Copenhagen'\n"
    "   - Local landmark examples: 'Bella Center' -> 'Bella Center, Copenhagen'\n\n"
    "   - try search online or translate English-Danish: 'War Museum' -> 'Krigsmuseet, Copenhagen'\n\n"
    "Water Flow Rate Rules (w):\n"
    "1. Always output in Liters per minute (L/min).\n"
    "2. If a number is given without units (e.g. 'need 4000'), assume it is L/min.\n"
    "3. If given in Gallons (e.g. '1000 Gal/min'), convert to Liters (1 Gallon = 3.78541 L).\n"
    "4. If no water requirement is mentioned, set water_lpm to 0."
)

def parse_with_openai(message: str) -> ParsedMessage:
    from openai import OpenAI
    
    client = OpenAI(api_key=OPENAI_API_KEY)
    completion = client.beta.chat.completions.parse(
        model="gpt-5.6",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": message}
        ],
        response_format=ParsedMessage,
    )
    return completion.choices[0].message.parsed

# 4. API 路由
@app.get("/")
def root():
    return {"status": "online", "current_provider": "openai", "docs": "/docs"}

@app.post("/parse-dispatch", response_model=APIResponse)
async def parse_dispatch_message(payload: dict):
    message = payload.get("message", "")
    if not message:
        raise HTTPException(status_code=400, detail="Empty message")

    try:
        parsed = parse_with_openai(message)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"OpenAI parsing failed: {str(e)}")

    lat = parsed.latitude
    lon = parsed.longitude

    if lat is None or lon is None:
        if parsed.location_name:
            try:
                lat, lon = geocode_location(parsed.location_name)
            except Exception as e:
                raise HTTPException(
                    status_code=422, 
                    detail=f"Geocoding failed for '{parsed.location_name}': {str(e)}"
                )
        elif payload.get("current_location"):
            try:
                lat, lon = payload["current_location"]
            except (TypeError, ValueError):
                raise HTTPException(status_code=422, detail="Invalid current_location")
        elif parsed.message_type in {"demand_update", "hydrant_failure", "failure_and_demand", "chatter"}:
            lat, lon = None, None
        else:
            raise HTTPException(
                status_code=422, 
                detail="No location or coordinates found in the message."
            )

    return APIResponse(
        x=round(lat, 6) if lat is not None else None,
        y=round(lon, 6) if lon is not None else None,
        w=round(parsed.water_lpm, 3),
        provider="openai",
        message_type=parsed.message_type,
        hydrant_id=parsed.hydrant_id,
        out_of_service=parsed.out_of_service,
        clarification_needed=parsed.clarification_needed,
        clarification=parsed.clarification,
    )
