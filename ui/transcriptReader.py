from typing import Optional, Tuple
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
import requests
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="Firefighter Dispatch Parser API")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 開發階段允許所有來源，生產環境可指定網站網址
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

ACTIVE_PROVIDER = "openai"  # 切換選項: "gemini" 或 "openai"

# 直接在此填入測試用的 API Key (不使用 .env 讀取)
GEMINI_API_KEY = "AQ.Ab8RN6IQKlCzTnWyzx7RLUZWhf_-lIgKfnZEC6P5yir13XSPXw"
OPENAI_API_KEY = "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"
# 初始化 Google GenAI Client
app = FastAPI(title="Firefighter Dispatch Parser API")

# 1. 結構化資料模型
class ParsedMessage(BaseModel):
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

class APIResponse(BaseModel):
    x: float = Field(..., description="Latitude")
    y: float = Field(..., description="Longitude")
    w: float = Field(..., description="Water requirement in L/min")
    provider: str = Field(..., description="LLM provider used")

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
    "Your job is to parse dispatch messages and extract exact coordinates (or landmark name) and water requirement.\n\n"
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

# 3. LLM 呼叫函式 (延遲初始化以防意外收費)
def parse_with_gemini(message: str) -> ParsedMessage:
    from google import genai
    from google.genai import types
    
    client = genai.Client(api_key=GEMINI_API_KEY)
    response = client.models.generate_content(
        model="gemini-3.6-flash",
        contents=message,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            response_mime_type="application/json",
            response_schema=ParsedMessage,
            temperature=0.0,
        ),
    )
    return ParsedMessage.model_validate_json(response.text)

def parse_with_openai(message: str) -> ParsedMessage:
    from openai import OpenAI
    
    client = OpenAI(api_key=OPENAI_API_KEY)
    completion = client.beta.chat.completions.parse(
        model="gpt-4o-mini",
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
    return {"status": "online", "current_provider": ACTIVE_PROVIDER, "docs": "/docs"}

@app.post("/parse-dispatch", response_model=APIResponse)
async def parse_dispatch_message(payload: dict):
    message = payload.get("message", "")
    if not message:
        raise HTTPException(status_code=400, detail="Empty message")

    try:
        if ACTIVE_PROVIDER == "openai":
            parsed = parse_with_openai(message)
        else:
            parsed = parse_with_gemini(message)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"LLM parsing failed ({ACTIVE_PROVIDER}): {str(e)}")

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
        else:
            raise HTTPException(
                status_code=422, 
                detail="No location or coordinates found in the message."
            )

    return APIResponse(
        x=round(lat, 6),
        y=round(lon, 6),
        w=round(parsed.water_lpm, 3),
        provider=ACTIVE_PROVIDER
    )