import os
import json
import random
import re
import base64
from datetime import datetime
from groq import Groq
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
import httpx
import edge_tts

os.environ['http_proxy'] = ''
os.environ['https_proxy'] = ''
os.environ['all_proxy'] = ''
os.environ['ALL_PROXY'] = ''

app = FastAPI()

clean_http_client = httpx.Client(trust_env=False)
client = Groq(
    api_key=os.environ.get("GROQ_API_KEY"),
    http_client=clean_http_client
)

if not os.path.exists("recordings"):
    os.makedirs("recordings")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

scores = {"A": 0, "B": 0}
current_theme = "Общая тема"
team_profiles = {
    "A": {"gender": "мужской", "age": 25},
    "B": {"gender": "женский", "age": 25}
}

async def text_to_speech_bytes(text: str) -> str:
    try:
        cleaned = re.sub(r'\(\+\d+\s*б\.\)', '', text)
        cleaned = re.sub(r'\(0\s*б\..*?\)', '', cleaned)
        cleaned = cleaned.replace("🔥", "").replace("🎤", "").strip()
        
        # Заменили голос Светланы на более четкий мужской голос Дмитрия, он звучит стабильнее
        communicate = edge_tts.Communicate(cleaned, "ru-RU-DmitryNeural")
        audio_bytes = b""
        async for chunk in communicate.stream():
            if chunk["type"] == "audio":
                audio_bytes += chunk["data"]
                
        return base64.b64encode(audio_bytes).decode('utf-8')
    except Exception as e:
        print(f"Ошибка TTS: {e}")
        return ""

def generate_academic_intro(theme: str):
    prompt = f"""
    Ты — строгий модератор интеллектуальных дебатов "NeuroRing".
    Тема дебатов: "{theme}".
    Напиши очень короткое, хлесткое вступление. Максимум 3 предложения.
    Стиль: саркастичный, профессиональный, литературный русский язык.
    Строго соблюдай правила русского языка, падежи и склонения.
    Закончи текст ровно этой фразой: "Минута на подготовку пошла."
    """
    try:
        response = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": prompt}]
        )
        return response.choices[0].message.content.strip()
    except Exception:
        return f"Тема нашей дискуссии: {theme}. Минута на подготовку пошла."

def transcribe_audio_pro(file_path):
    try:
        with open(file_path, "rb") as file:
            return client.audio.transcriptions.create(
                file=(file_path, file.read()),
                model="whisper-large-v3",
                response_format="text",
                language="ru" 
            )
    except Exception as e:
        print(f"Ошибка Whisper: {e}")
        return ""

def analyze_and_judge(transcript, current_team, theme):
    opponent_team = "B" if current_team == "A" else "A"
    
    prompt = f"""
    Ты — строгий, циничный судья дебатов "NeuroRing".
    Тема: "{theme}".
    
    Реплика спикера из команды {current_team}: "{transcript}"
    
    ТВОЯ РОЛЬ:
    Оцени аргумент по 10-балльной шкале. 
    Дай короткий, острый комментарий (максимум 2 предложения).
    НИКАКОЙ ФАМИЛЬЯРНОСТИ (запрещено использовать слова "красотка", "парень", "дружище").
    Строго соблюдай падежи, склонения и правила русского языка.
    Затем коротко передай ход Команде {opponent_team}.
    
    ОТВЕТЬ СТРОГО В ФОРМАТЕ JSON:
    {{
        "comment": "Твоя строгая и саркастичная оценка аргумента.",
        "pass_turn": "Короткая фраза передачи хода",
        "points": 0
    }}
    """
    try:
        response = client.chat.completions.create(
            model="llama-3.1-8b-instant",
            messages=[{"role": "user", "content": prompt}]
        )
        raw_text = response.choices[0].message.content.strip()
        json_match = re.search(r'\{.*\}', raw_text, re.DOTALL)
        if json_match:
            return json.loads(json_match.group(0))
        else:
            raise ValueError("JSON не найден")
    except Exception as e:
        print(f"Ошибка Llama: {e}")
        return {"comment": "Аргумент принят к сведению.", "pass_turn": f"Ход Команды {opponent_team}.", "points": 5}

@app.websocket("/ws/debate")
async def debate_endpoint(websocket: WebSocket):
    global current_theme, team_profiles
    await websocket.accept()
    print("🎭 ПОДКЛЮЧЕНИЕ К КАНАЛУ ДЕБАТОВ УСТАНОВЛЕНО")
    current_team = "unknown"

    try:
        while True:
            message = await websocket.receive()
            
            if message.get("type") == "websocket.disconnect":
                print("🔴 Клиент разорвал соединение")
                break
            
            if "text" in message:
                text_data = message["text"]
                
                if text_data.startswith("START_GAME:"):
                    raw_json = text_data.split("START_GAME:")[1]
                    game_config = json.loads(raw_json)
                    
                    current_theme = game_config["theme"]
                    team_profiles["A"] = game_config["profiles"]["A"]
                    team_profiles["B"] = game_config["profiles"]["B"]
                    
                    print(f"📌 Настройки: Тема [{current_theme}]")
                    
                    intro_speech = generate_academic_intro(current_theme)
                    audio_b64 = await text_to_speech_bytes(intro_speech)
                    
                    data_packet = {
                        "transcript": "Презентация темы", "text": intro_speech,
                        "scoreA": scores["A"], "scoreB": scores["B"], "activeTeam": "unknown",
                        "audio": audio_b64,
                        "event": "INTRO_COMPLETE"
                    }
                    await websocket.send_text(json.dumps(data_packet))
                    
                elif text_data.startswith("TEAM_SIGNAL:"):
                    # ИСПРАВЛЕНИЕ: Четкое определение команды
                    if "А" in text_data or "A" in text_data: # Русская и английская А
                        current_team = "A"
                    else:
                        current_team = "B"
            
            elif "bytes" in message:
                audio_data = message["bytes"]
                timestamp = datetime.now().strftime("%H-%M-%S")
                filename = f"recordings/Team{current_team}_{timestamp}.webm"
                
                with open(filename, "wb") as f:
                    f.write(audio_data)

                transcript = transcribe_audio_pro(filename)
                
                if not transcript or len(transcript.strip()) < 5:
                    text_fallback = "Микрофон передан, но я ничего не услышал."
                    audio_b64 = await text_to_speech_bytes(text_fallback)
                    data_packet = {
                        "transcript": "...", "text": text_fallback,
                        "scoreA": scores["A"], "scoreB": scores["B"], "activeTeam": current_team,
                        "audio": audio_b64
                    }
                else:
                    analysis = analyze_and_judge(transcript, current_team, current_theme)
                    
                    points = analysis.get("points", 0)
                    comment = analysis.get("comment", "Принято.")
                    pass_turn = analysis.get("pass_turn", "Слово другой команде.")
                    
                    scores[current_team] += points
                    points_alert = f"(+{points} б.)" if points > 0 else "(0 б.)"
                    full_host_response = f"{comment} {points_alert}\n\n🎤 {pass_turn}"

                    audio_b64 = await text_to_speech_bytes(f"{comment} {pass_turn}")

                    data_packet = {
                        "transcript": transcript, "text": full_host_response,
                        "scoreA": scores["A"], "scoreB": scores["B"], "activeTeam": current_team,
                        "audio": audio_b64
                    }
                
                await websocket.send_text(json.dumps(data_packet))

    except WebSocketDisconnect:
        print("🔴 Вебсокет закрыт (FastAPI)")
    except Exception as e:
        print(f"💥 Ошибка внутри вебсокета: {e}")

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)