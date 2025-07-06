# config.py

import os

# === Discord Bot Token ===
# IMPORTANTE: Mover este token para um arquivo .env é a prática recomendada para segurança.
# Crie um arquivo .env na raiz e adicione: BOT_TOKEN="seu_token_aqui"
# O bot tentará carregar do .env primeiro. Este valor aqui é um fallback.
BOT_TOKEN = os.getenv("BOT_TOKEN", "SEU_TOKEN_AQUI_COMO_FALLBACK")

# === IDs do Servidor e Canais ===
# REMOVIDO! Estas IDs não são mais globais. Elas serão determinadas dinamicamente
# para cada partida e armazenadas na sua respectiva GameInstance.

# === Caminhos para Arquivos e Pastas ===
# Usar os.path.join para compatibilidade entre sistemas operacionais (Windows/Linux/Mac)
ASSETS_PATH = "assets"
IMAGES_PATH = os.path.join(ASSETS_PATH, "images")
AUDIO_PATH = os.path.join(ASSETS_PATH, "audio")
DATA_PATH = "data"
RANKING_FILE = os.path.join(DATA_PATH, "ranking.json")

# === Configuração de Imagens de Evento ===
EVENT_IMAGES = {
    "NIGHT_START": "night.png",
    "DAY_SAFE": "day_safe.png",
    "DAY_DEATH": "day_death.png",
    "DAY_REVIVAL": "day_revival.png",
    "PLAGUE_KILL": "plague_kill.png",
    "CITY_WIN": "city_win.png",
    "VILLAINS_WIN": "villains_win.png",
    "CLOWN_WIN": "clown_win.png",
    "LOVERS_WIN": "lovers_win.png",
    "PLAGUE_WIN": "plague_win.png",
    "CORRUPTOR_WIN": "corruptor_win.png",
    "HEADHUNTER_WIN": "headhunter_win.png",
}

# === Configurações do Jogo ===
MIN_PLAYERS = 5
MAX_PLAYERS = 16
NIGHT_DURATION_SECONDS = 60
DAY_DISCUSSION_DURATION_SECONDS = 45
VOTE_DURATION_SECONDS = 30
MAX_GAME_NIGHTS = 7

# === Configuração de Áudios ===
AUDIO_ENABLED = True
AUDIO_FILES = {
    "DAY_START": ["day_start.mp3"],
    "NIGHT_START": ["night_start.mp3"],
    "VOTE_START": ["vote_start.mp3"],
    "CITY_WIN": ["city_win.mp3"],
    "VILLAINS_WIN": ["villains_win.mp3"],
    "SHERIFF_WIN": ["sheriff_win.mp3"],
    "GAME_LOSE": ["game_lose.mp3"],
    "SHERIFF_SHOT": ["sheriff_shot.mp3"],
    "PLAYER_DEATH": ["player_death_1.mp3", "player_death_2.mp3", "player_death_3.mp3"],
    "PLAYER_REVIVE": ["player_revive.mp3"],
    "PROTECTION_SUCCESS": ["protection_success.mp3"],
    "CLOWN_WIN": ["clown_win.mp3"],
    "LOVERS_WIN": ["lovers_win.mp3"],
    "PLAGUE_WIN": ["plague_win.mp3"],
    "CORRUPTOR_WIN": ["corruptor_win.mp3"],
    "HEADHUNTER_WIN": ["headhunter_win.mp3"],
}

# === Sistema de Composição e Sorteio de Papéis ===
GAME_COMPOSITIONS = {
    "5": {"Cidade": 4, "Vilões": 1, "Solo": 0},
    "6": {"Cidade": 5, "Vilões": 1, "Solo": 0},
    "7": {"Cidade": 5, "Vilões": 1, "Solo": 1},
    "8": {"Cidade": 5, "Vilões": 2, "Solo": 1},
    "9": {"Cidade": 5, "Vilões": 2, "Solo": 2},
    "10": {"Cidade": 6, "Vilões": 3, "Solo": 1},
    "11": {"Cidade": 6, "Vilões": 3, "Solo": 2},
    "12": {"Cidade": 7, "Vilões": 3, "Solo": 2},
    "13": {"Cidade": 7, "Vilões": 3, "Solo": 3},
    "14": {"Cidade": 8, "Vilões": 4, "Solo": 2},
    "15": {"Cidade": 8, "Vilões": 4, "Solo": 3},
    "16": {"Cidade": 9, "Vilões": 4, "Solo": 3},
}

ROLE_POOL = {
    "Cidade": {
        "essenciais": ["Prefeito", "Guarda-costas", "Xerife", "Anjo"],
        "investigadores": ["Detetive", "Vidente de Aura", "Médium"]
    },
    "Vilões": {
        "essenciais": ["Assassino Alfa"],
        "outros": ["Assassino Júnior", "Cúmplice"]
    },
    "Solo": {
        "exclusivos": ["Palhaço", "Caçador de Cabeças"], # Apenas um destes pode aparecer por jogo
        "outros": ["Bruxo", "Fofoqueiro", "Cupido", "Praga", "Corruptor"]
    }
}

# === Mensagens ===
MSG_BOT_STARTING = "Ligando os motores... e me preparando para gerenciar múltiplas realidades de treta!"