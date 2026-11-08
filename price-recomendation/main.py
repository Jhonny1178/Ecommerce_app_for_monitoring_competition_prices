import os
import json
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv
from groq_manager import GroqManager

from algorytm import ustal_cene
from explanation import (
    build_reason,
    contradicts_facts,
    facts_for_prompt,
    fallback_reason,
    limit_sentences,
    market_facts,
    mentions_amount,
)

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
class ZmianaCeny(BaseModel):
    sklep: str
    cena_przed: float
    cena_po: float
    data: str


class DaneZeScrapera(BaseModel):
    nazwa_produktu: str
    lista_rynkowa: list[float]
    nasza_cena: float
    # Opcjonalne: tylko oferty, których cena faktycznie się zmieniła (ostatnie 30 dni).
    historia_cen: list[ZmianaCeny] = []


def skonsultuj_z_llm(nazwa, facts, history=()):
    manager = GroqManager()

    prompt = f"""
    Produkt: "{nazwa}"

    Fakty (już policzone, nie zmieniaj liczb):
    {facts_for_prompt(facts, history)}

    Algorytm ustawia cenę tuż poniżej oferty najbliższej średniej rynkowej: sklep jest konkurencyjny,
    ale nie schodzi do najtańszej oferty i nie traci niepotrzebnie marży.

    Zadanie:
    1. Oceń, czy cena wyliczona przez algorytm ma sens dla tego produktu. Zmień ją tylko z bardzo ważnego powodu biznesowego.
    2. Napisz uzasadnienie dla właściciela sklepu: 1–2 krótkie zdania, prostym językiem.
       - Pisz w drugiej osobie ("jesteś", "Twoja oferta"), ale nie zaczynaj zdania od słowa "Ty".
       - Nie powtarzaj decyzji o cenie (zdanie "Obniż/Podnieś/Zostaw cenę..." dodamy sami), napisz tylko, dlaczego ta cena ma sens na tle konkurencji.
       - Nie opisuj działania algorytmu ani obliczeń.
       - Pisz tylko o tym, co wynika z faktów, i nie wspominaj, że jakichś danych brakuje.
       - Używaj tylko liczb z faktów. Jeśli zmieniasz cenę, nie wspominaj ceny wyliczonej przez algorytm.

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

    chat_completion = manager.execute_with_fallback(_call_groq)
    return json.loads(chat_completion.choices[0].message.content)


@app.post("/api/oblicz-cene")
def oblicz_cene_endpoint(dane: DaneZeScrapera):
    try:
        wynik_algorytmu = ustal_cene(dane.lista_rynkowa, dane.nasza_cena)
        facts = market_facts(dane.lista_rynkowa, dane.nasza_cena, wynik_algorytmu)

        if not dane.lista_rynkowa:
            return {"cena_ostateczna": wynik_algorytmu, "uzasadnienie": fallback_reason(facts)}

        history = [zmiana.model_dump() for zmiana in dane.historia_cen]

        try:
            analiza = skonsultuj_z_llm(dane.nazwa_produktu, facts, history)
        except Exception as e:
            # Bez AI klient i tak dostaje cenę z algorytmu i uzasadnienie z faktów.
            print(f"[REKOMENDACJA] Model AI niedostępny, uzasadnienie z faktów: {e}", flush=True)
            return {"cena_ostateczna": wynik_algorytmu, "uzasadnienie": fallback_reason(facts)}

        cena = analiza.get("cena_ostateczna")
        powod = limit_sentences(analiza.get("uzasadnienie"), 2)
        cena_ok = (
            not isinstance(cena, bool)
            and isinstance(cena, (int, float))
            and cena > 0
            # Odchylenie o ponad 30% od algorytmu traktujemy jako błąd modelu, nie decyzję biznesową.
            and abs(cena - wynik_algorytmu) <= 0.3 * wynik_algorytmu
        )
        if not powod or not cena_ok:
            # Tekst z faktów opisuje cenę z algorytmu, więc obie wartości muszą pochodzić z niego.
            return {"cena_ostateczna": wynik_algorytmu, "uzasadnienie": fallback_reason(facts)}

        final_facts = market_facts(dane.lista_rynkowa, dane.nasza_cena, cena)
        # Tekst o cenie z algorytmu (klient jej nie widzi) albo sprzeczny z faktami zastępujemy faktami.
        if (cena != wynik_algorytmu and mentions_amount(powod, wynik_algorytmu)) or contradicts_facts(
            powod, final_facts, has_history=bool(history)
        ):
            return {"cena_ostateczna": cena, "uzasadnienie": fallback_reason(final_facts)}

        return {"cena_ostateczna": cena, "uzasadnienie": build_reason(dane.nasza_cena, cena, powod)}

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
