import os
import json
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv
from groq_manager import GroqManager

from algorytm import ustal_cene

load_dotenv()

app = FastAPI()

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# Definicja Kontraktu
class DaneZeScrapera(BaseModel):
    nazwa_produktu: str
    lista_rynkowa: list[float]
    nasza_cena: float

with open("algorytm.py", "r", encoding="utf-8") as f:
    KOD_ALGORYTMU = f.read()


def skonsultuj_z_llm(nazwa, liczby, nasza_cena, cena_z_algorytmu, kod_algorytmu):
    manager = GroqManager()

    prompt = f"""
    Przeanalizuj dane cenowe dla produktu: "{nazwa}"
    - Ceny konkurencji (lista): {liczby}
    - Nasza aktualna cena: {nasza_cena}
    - Cena zaproponowana przez algorytm: {cena_z_algorytmu}

    Oto kod algorytmu, który wyliczył tę cenę:
    {kod_algorytmu}

    Zadanie:
    1. Wyjaśnij krótko, dlaczego algorytm podjął suchą decyzję na podstawie załączonego kodu.
    2. Oceń sensowność tej ceny dla produktu "{nazwa}".
    3. Podaj cenę ostateczną (zmień ją tylko, jeśli masz bardzo ważny powód biznesowy).

    Zwróć wynik wyłącznie jako JSON:
    {{
        "cena_ostateczna": <liczba>,
        "uzasadnienie": "<tekst>"
    }}
    """
    
    def _call_groq(client):
        return client.chat.completions.create(
            messages=[
                {"role": "system", "content": "Jesteś ekspertem ds. strategii cenowych. Odpowiadasz tylko w JSON."},
                {"role": "user", "content": prompt}
            ],
            model="openai/gpt-oss-120b",
            response_format={"type": "json_object"},
            temperature=0.2
        )
        
    try:
        chat_completion = manager.execute_with_fallback(_call_groq)
        return json.loads(chat_completion.choices[0].message.content)
    except Exception as e:
        if "Wszystkie klucze API Groq zostały wykorzystane" in str(e):
            return {"cena_ostateczna": cena_z_algorytmu, "uzasadnienie": "Chwilowo ta usługa AI jest niedostępna z powodu limitów API. Użyto surowego wyniku algorytmu matematycznego."}
        raise e


@app.post("/api/oblicz-cene")
def oblicz_cene_endpoint(dane: DaneZeScrapera):
    try:
        wynik_algorytmu = ustal_cene(dane.lista_rynkowa, dane.nasza_cena)

        analiza = skonsultuj_z_llm(
            dane.nazwa_produktu,
            dane.lista_rynkowa,
            dane.nasza_cena,
            wynik_algorytmu,
            KOD_ALGORYTMU
        )
        return analiza

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))