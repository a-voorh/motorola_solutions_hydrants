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
    allow_origins=["*"],  # Restrict this to the deployed UI origin in production.
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

# 1. Structured data model
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
    demand_is_incremental: bool = Field(
        default=False,
        description=(
            "True when the message explicitly adds to the active demand, such as "
            "'increase by 500 L/min' or 'add another 500'. False when the number "
            "is an absolute replacement total."
        ),
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

# 2. Geocoding
def geocode_location(query: str) -> Tuple[float, float]:
    url = "https://nominatim.openstreetmap.org/search"
    headers = {"User-Agent": "WaterSupplyAssistant/1.0"}
    params = {
        "q": query, 
        "format": "json", 
        "limit": 1
    }
    
    resp = requests.get(url, params=params, headers=headers, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    if not data:
        raise ValueError(f"Could not geocode location: {query}")
    return float(data[0]["lat"]), float(data[0]["lon"])

# 3. System prompt
SYSTEM_PROMPT = (
    "You interpret noisy, informal fire-dispatch messages for the Copenhagen / Denmark metropolitan area.\n"
    "Extract only facts supported by the message. Correct ordinary speech-recognition errors, spelling mistakes, abbreviations, missing punctuation, and minor word-order problems, but never invent a location, hydrant ID, or water requirement.\n\n"
    "Classify the message as exactly one of: initial_request, demand_update, hydrant_failure, failure_and_demand, chatter.\n"
    "- initial_request: a new incident request containing a water requirement, optionally with a location.\n"
    "- demand_update: an absolute replacement total for an active incident, such as 'increase demand to 5000 L/min' or 'we now need five thousand'.\n"
    "- hydrant_failure: an explicitly unavailable hydrant, such as 'H0479 is out of service', 'we lost hydrant 479', or 'hydrant four seven nine is not working'. Normalize the ID to H0479.\n"
    "- failure_and_demand: one message explicitly reports both an unavailable hydrant and an absolute new demand.\n"
    "- chatter: greetings, status information, restoration reports, unsupported requests, or messages with no location, water requirement, or hydrant action. Pure chatter must not set clarification_needed.\n\n"
    "Demand updates have two forms. 'Increase demand to 5000' or 'we now need 5000' is an absolute replacement total and must set demand_is_incremental=false. 'Increase demand by 500', 'add another 500', or similar wording is an increment to the active incident's current demand and must set demand_is_incremental=true. The application adds that delta to its active demand; do not set clarification_needed merely because the current total is not written in the message.\n"
    "Restoration messages such as 'back in service', 'repaired', or 'available again' are unsupported and must be chatter.\n"
    "Update messages may omit a location because the active incident supplies it. If a state-changing message is ambiguous about the hydrant or absolute demand, set clarification_needed=true, provide one short clarification question, and leave the uncertain fields empty.\n\n"
    "Location rules:\n"
    "1. If explicit decimal coordinates are provided, return them as latitude and longitude.\n"
    "2. Otherwise, normalize a landmark, station, street, or square into a useful geocoder query in its full Danish or English form, adding ', Copenhagen' when helpful. Do not fabricate coordinates.\n"
    "3. Examples: 'Kogens nytory' or 'Kongens Nytorf' -> 'Kongens Nytorv, Copenhagen'; 'KBH H' or 'Central Station' -> 'København H, Copenhagen'; 'War Museum' -> 'Krigsmuseet, Copenhagen'.\n\n"
    "Water requirements must be returned in water_lpm. A bare number is L/min. Convert US gallons per minute using 1 US gallon = 3.78541 liters. If no requirement is mentioned, return 0."
)

def parse_with_openai(message: str) -> ParsedMessage:
    from openai import OpenAI

    client = OpenAI(api_key=OPENAI_API_KEY, timeout=20.0, max_retries=1)
    completion = client.beta.chat.completions.parse(
        model="gpt-5.6",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": message}
        ],
        response_format=ParsedMessage,
    )
    return completion.choices[0].message.parsed

# 4. API routes
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
