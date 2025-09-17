# cogs/actions.py

import discord
from discord.ext import commands
from discord import option, ApplicationContext
import logging
import asyncio
import random
import os
from typing import Optional, List, Dict, Any

import config
from .game_instance import GameInstance, PlayerState
from .utils import send_dm_safe, send_public_message
from roles.base_role import Role
from roles.cidade_roles import GuardaCostas, Detetive, Anjo, Xerife, Prefeito, Medium, VidenteDeAura, CidadaoComum
from roles.viloes_roles import AssassinoAlfa, AssassinoJunior, Cumplice, AssassinoSimples
from roles.solo_roles import Palhaco, Fofoqueiro, Bruxo, Cupido, Praga, Corruptor, CacadorDeCabecas

logger = logging.getLogger(__name__)

# --- Funções de Autocomplete (Agora usam o contexto para encontrar o jogo) ---

async def search_alive_players(ctx: discord.AutocompleteContext) -> list:
    if isinstance(ctx.interaction.channel, discord.DMChannel):
        game = ctx.bot.game_manager.get_game_by_player(ctx.interaction.user.id)
    else:
        game = ctx.bot.game_manager.get_game(ctx.interaction.channel_id)
    if not game: return []
    return [p.member.display_name for p in game.get_alive_players_states() if p.member.display_name.lower().startswith(ctx.value.lower())]

async def search_dead_players(ctx: discord.AutocompleteContext) -> list:
    if isinstance(ctx.interaction.channel, discord.DMChannel):
        game = ctx.bot.game_manager.get_game_by_player(ctx.interaction.user.id)
    else:
        game = ctx.bot.game_manager.get_game(ctx.interaction.channel_id)
    if not game: return []
    return [p.member.display_name for p in game.players.values() if not p.is_alive and p.member.display_name.lower().startswith(ctx.value.lower())]

# --- Funções de Ajuda (Agora recebem a instância do jogo como argumento) ---

def find_player_by_name(game: GameInstance, name: str, alive_only: bool = True) -> Optional[discord.Member]:
    name_lower = name.lower()
    for player_state in game.players.values():
        player = player_state.member
        if player.display_name.lower() == name_lower or str(player).lower() == name_lower:
            if (alive_only and player_state.is_alive) or not alive_only:
                return player
    matches = [p_state.member for p_state in game.players.values() if p_state.member.display_name.lower().startswith(name_lower)]
    final_matches = [m for m in matches if (p_state := game.get_player_state_by_id(m.id)) and ((alive_only and p_state.is_alive) or not alive_only)]
    if len(final_matches) == 1: return final_matches[0]
    return None

def find_dead_player_by_name(game: GameInstance, name: str) -> Optional[discord.Member]:
    player_state = next((ps for ps in game.players.values() if (ps.member.display_name.lower() == name.lower() or str(ps.member).lower() == name.lower()) and not ps.is_alive), None)
    return player_state.member if player_state else None

# --- Decorators para Checagens (Agora encontram a instância do jogo e a anexam ao contexto) ---

def get_game_instance(ctx: ApplicationContext) -> Optional[GameInstance]:
    if isinstance(ctx.channel, discord.DMChannel):
        return ctx.bot.game_manager.get_game_by_player(ctx.author.id)
    else:
        return ctx.bot.game_manager.get_game(ctx.channel.id)

def game_check(check_function):
    async def predicate(ctx: ApplicationContext):
        game = get_game_instance(ctx)
        if not game:
            await ctx.respond("Não encontrei uma partida ativa para você ou neste canal.", ephemeral=True)
            return False
        ctx.game = game
        return await check_function(ctx, game)
    return commands.check(predicate)

def check_game_phase(allowed_phases: List[str]):
    async def check(ctx, game: GameInstance):
        if game.current_phase not in allowed_phases:
            await ctx.respond(f"Ação inválida para a fase atual ({game.current_phase}).", ephemeral=True)
            return False
        return True
    return game_check(check)

def check_player_state(requires_alive: bool = True, requires_dm: bool = True):
    async def check(ctx, game: GameInstance):
        if requires_dm and ctx.command.name not in ["disparar", "sabotar", "fraudar"] and not isinstance(ctx.channel, discord.DMChannel):
            await ctx.respond("Essa ação é secreta! Use este comando na nossa conversa privada (DM).", ephemeral=True)
            return False
        player_state = game.get_player_state_by_id(ctx.author.id)
        if not player_state:
            await ctx.respond("Você não está nesta partida.", ephemeral=True)
            return False
        if requires_alive and not player_state.is_alive and ctx.command.name != "assombrar":
            await ctx.respond("Fantasmas não podem fazer ações. 👻", ephemeral=True)
            return False
        return True
    return game_check(check)

def check_role(allowed_roles: List[type]):
    async def check(ctx, game: GameInstance):
        player_state = game.get_player_state_by_id(ctx.author.id)
        if not player_state or not player_state.role:
            await ctx.respond("Erro: Não foi possível encontrar seu papel no jogo.", ephemeral=True)
            return False
        if not allowed_roles: return True
        if not isinstance(player_state.role, tuple(allowed_roles)):
            await ctx.respond("Você não tem o papel necessário para usar este comando.", ephemeral=True)
            return False
        if game.is_night() and player_state.is_corrupted:
            await ctx.respond("Sua mente está turva... Você não consegue usar suas habilidades esta noite.", ephemeral=True)
            return False
        return True
    return game_check(check)

def check_is_ghost():
    async def check(ctx, game: GameInstance):
        player_state = game.get_player_state_by_id(ctx.author.id)
        if not player_state or not player_state.is_ghost:
            await ctx.respond("Apenas Fantasmas podem assombrar...", ephemeral=True)
            return False
        return True
    return game_check(check)

def record_night_action(game: GameInstance, player_id: int, role: Role, action_name: str, target_id: Optional[int] = None, priority: int = 50, **kwargs):
    game.night_actions[player_id] = {"action": action_name, "target_id": target_id, "role": role, "priority": priority, **kwargs}
    logger.info(f"[Jogo #{game.text_channel.id}] Ação noturna '{action_name}' registrada para {player_id} -> {target_id}")

class ActionsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        logger.info("Cog Actions carregado.")

    async def distribute_initial_info(self, game: GameInstance):
        logger.info(f"[Jogo #{game.text_channel.id}] Distribuindo informações iniciais da Noite 1.")
        tasks = []
        villains = [p_state for p_state in game.players.values() if p_state.role and p_state.role.faction == "Vilões"]
        if len(villains) > 1:
            villain_names = [v.member.display_name for v in villains]
            for villain_state in villains:
                other_villains = [name for name in villain_names if name != villain_state.member.display_name]
                if other_villains:
                    message = f"Olá, {villain_state.role.name}. 🤫 Seus parceiros no crime são: **{', '.join(other_villains)}**."
                    tasks.append(send_dm_safe(villain_state.member, message=message))
        elif len(villains) == 1:
            message = f"Olá, {villains[0].role.name}. Você é a única ameaça da sua facção. Aja com cuidado."
            tasks.append(send_dm_safe(villains[0].member, message=message))
        await asyncio.gather(*tasks)

    # --- COMANDOS ---
    
    @commands.slash_command(name="decreto", description="(Prefeito) Amplifica o poder de voto da Cidade (1x por jogo).")
    @check_game_phase(["day_voting"])
    @check_player_state()
    @check_role([Prefeito])
    async def decreto(self, ctx: ApplicationContext):
        game = ctx.game
        player_state = game.get_player_state_by_id(ctx.author.id)
        if player_state.is_confused:
            await ctx.respond("😵‍💫 **Que tontura!** Sua ação saiu toda errada.", ephemeral=True)
            return
        if game.decreto_used:
            await ctx.respond("Você já usou seu Decreto nesta partida.", ephemeral=True)
            return

        game.decreto_used = True
        game.decreto_active = True
        game.sabotage_blocked = True
        await send_public_message(self.bot, game.text_channel, "O Prefeito invocou um **DECRETO DE EMERGÊNCIA**!")
        await ctx.respond("Seu Decreto foi proclamado!", ephemeral=True)

    @commands.slash_command(name="sabotar", description="(Assassino Alfa) Pula o dia e vai direto para a noite (1x por jogo).")
    @check_game_phase(["day_discussion", "day_voting"])
    @check_player_state(requires_dm=False)
    @check_role([AssassinoAlfa])
    async def sabotar(self, ctx: ApplicationContext):
        game = ctx.game
        if game.sabotage_blocked:
            await ctx.respond("Você não pode sabotar durante um Decreto de Emergência!", ephemeral=True)
            return
        if game.sabotage_used:
            await ctx.respond("A sabotagem já foi usada nesta partida.", ephemeral=True)
            return

        game.sabotage_used = True
        game_flow_cog = self.bot.get_cog("GameFlowCog")
        if game_flow_cog:
            await ctx.respond("Sabotagem ativada!", ephemeral=True)
            await send_public_message(self.bot, game.text_channel, "🚨 **SABOTAGEM!** 🚨 O dia é interrompido bruscamente...")
            await game_flow_cog.force_night(game)
        else:
            await ctx.respond("Erro: Não foi possível contatar o controle de fluxo do jogo.", ephemeral=True)
            logger.error(f"[Jogo #{game.text_channel.id}] CRÍTICO: GameFlowCog não encontrado para /sabotar.")
    
    @commands.slash_command(name="fraudar", description="(Cúmplice) Embaralha os votos da votação atual (1x por jogo).")
    @check_game_phase(["day_voting"])
    @check_player_state(requires_dm=False)
    @check_role([Cumplice])
    async def fraudar(self, ctx: ApplicationContext):
        game = ctx.game
        if game.fraud_used:
            await ctx.respond("Você já usou sua habilidade de fraudar nesta partida.", ephemeral=True)
            return
        game.fraud_used = True
        game.fraud_active = True
        await ctx.respond("Fraude ativada! Os resultados serão... inesperados.", ephemeral=True)
        await send_public_message(self.bot, game.text_channel, "🎭 Uma onda de desinformação se espalha! A votação foi comprometida...")

    @commands.slash_command(name="possuir", description="(Assassino Alfa) Tenta converter um jogador para a sua facção.")
    @check_game_phase(["night"])
    @check_player_state()
    @check_role([AssassinoAlfa])
    @option("jogador", description="O alvo da sua influência maligna.", autocomplete=search_alive_players)
    async def possuir(self, ctx: ApplicationContext, jogador: str):
        game = ctx.game
        if len(game.players) < 11:
            await ctx.respond("A habilidade de Possuir só está disponível com 11+ jogadores.", ephemeral=True)
            return
        target_member = find_player_by_name(game, jogador)
        if not target_member: await ctx.respond(f"Não achei o jogador vivo '{jogador}'.", ephemeral=True); return
        target_state = game.get_player_state_by_id(target_member.id)
        if target_state.role.faction == "Vilões":
            await ctx.respond("Você não pode possuir quem já está do seu lado.", ephemeral=True); return
        player_state = game.get_player_state_by_id(ctx.author.id)
        record_night_action(game, ctx.author.id, player_state.role, "possess", target_member.id, priority=90)
        await ctx.respond(f"Sua influência maligna se espalha em direção a **{target_member.display_name}**.", ephemeral=True)

    @commands.slash_command(name="comparar", description="(Fofoqueiro) Vê se dois jogadores são do mesmo time (2x por jogo).")
    @check_game_phase(["night"])
    @check_player_state()
    @check_role([Fofoqueiro])
    @option("jogador1", description="O primeiro jogador para comparar.", autocomplete=search_alive_players)
    @option("jogador2", description="O segundo jogador para comparar.", autocomplete=search_alive_players)
    async def comparar(self, ctx: ApplicationContext, jogador1: str, jogador2: str):
        game = ctx.game
        uses = game.fofoqueiro_comparisons.get(ctx.author.id, 0)
        if uses >= 2: await ctx.respond("Você já usou sua fofoca comparativa duas vezes.", ephemeral=True); return
        target1 = find_player_by_name(game, jogador1)
        target2 = find_player_by_name(game, jogador2)
        if not target1 or not target2: await ctx.respond(f"Não encontrei um ou ambos: '{jogador1}', '{jogador2}'.", ephemeral=True); return
        if target1.id == target2.id: await ctx.respond("Escolha dois jogadores diferentes.", ephemeral=True); return
        if target1.id == ctx.author.id or target2.id == ctx.author.id: await ctx.respond("Você não pode se incluir.", ephemeral=True); return
        target1_state = game.get_player_state_by_id(target1.id)
        target2_state = game.get_player_state_by_id(target2.id)
        are_same_faction = target1_state.role.faction == target2_state.role.faction
        result_message = "são da mesma facção" if are_same_faction else "NÃO são da mesma facção"
        game.fofoqueiro_comparisons[ctx.author.id] = uses + 1
        await ctx.respond(f"Sua investigação revelou: **{target1.display_name}** e **{target2.display_name}** {result_message}. ({uses + 1}/2 usos)", ephemeral=True)

    @commands.slash_command(name="apaixonar", description="(Cupido) Escolha dois jogadores para se apaixonarem (Noite 1).")
    @check_game_phase(["night"])
    @check_player_state()
    @check_role([Cupido])
    @option("jogador1", description="O primeiro alvo do seu feitiço de amor.", autocomplete=search_alive_players)
    @option("jogador2", description="O segundo alvo do seu feitiço de amor.", autocomplete=search_alive_players)
    async def apaixonar(self, ctx: ApplicationContext, jogador1: str, jogador2: str):
        game = ctx.game
        if game.current_night != 1: await ctx.respond("Você só pode usar essa magia na primeira noite!", ephemeral=True); return
        target1 = find_player_by_name(game, jogador1)
        target2 = find_player_by_name(game, jogador2)
        if not target1 or not target2: await ctx.respond(f"Não encontrei um ou ambos: '{jogador1}', '{jogador2}'.", ephemeral=True); return
        if target1 == target2: await ctx.respond("Escolha dois jogadores diferentes!", ephemeral=True); return
        player_state = game.get_player_state_by_id(ctx.author.id)
        record_night_action(game, ctx.author.id, player_state.role, "cupid_match", priority=10, lover1_id=target1.id, lover2_id=target2.id)
        await ctx.respond(f"Flecha disparada! 🏹 Você escolheu {target1.display_name} e {target2.display_name}.", ephemeral=True)

    @commands.slash_command(name="proteger", description="(Guarda-costas) Escolha um jogador para proteger esta noite.")
    @check_game_phase(["night"])
    @check_player_state()
    @check_role([GuardaCostas])
    @option("jogador", description="O jogador que você quer proteger.", autocomplete=search_alive_players)
    async def proteger(self, ctx: ApplicationContext, jogador: str):
        game = ctx.game
        target_member = find_player_by_name(game, jogador)
        if not target_member: await ctx.respond(f"Não achei o jogador vivo '{jogador}'.", ephemeral=True); return
        if target_member.id == ctx.author.id: await ctx.respond("Você não pode proteger a si mesmo.", ephemeral=True); return
        if game.last_protected_target.get(ctx.author.id) == target_member.id: await ctx.respond("Você já protegeu essa pessoa na noite passada.", ephemeral=True); return
        player_state = game.get_player_state_by_id(ctx.author.id)
        record_night_action(game, ctx.author.id, player_state.role, "protect", priority=20, target_id=target_member.id)
        game.last_protected_target[ctx.author.id] = target_member.id
        await ctx.respond(f"Entendido! Você montará guarda para {target_member.display_name} esta noite.", ephemeral=True)

    @commands.slash_command(name="corromper", description="(Corruptor) Bloqueia a habilidade de um jogador esta noite.")
    @check_game_phase(["night"])
    @check_player_state()
    @check_role([Corruptor])
    @option("jogador", description="O jogador que você quer corromper.", autocomplete=search_alive_players)
    async def corromper(self, ctx: ApplicationContext, jogador: str):
        game = ctx.game
        target_member = find_player_by_name(game, jogador)
        if not target_member: await ctx.respond(f"Não achei o jogador vivo '{jogador}'.", ephemeral=True); return
        if target_member.id == ctx.author.id: await ctx.respond("Tentar corromper a si mesmo? Ousado...", ephemeral=True); return
        if game.last_corrupted_target.get(ctx.author.id) == target_member.id: await ctx.respond("Você já corrompeu essa pessoa na noite passada.", ephemeral=True); return
        player_state = game.get_player_state_by_id(ctx.author.id)
        record_night_action(game, ctx.author.id, player_state.role, "corrupt", target_member.id, priority=15)
        game.last_corrupted_target[ctx.author.id] = target_member.id
        await ctx.respond(f"Você tentará corromper a mente de {target_member.display_name} esta noite.", ephemeral=True)

    @commands.slash_command(name="confundir", description="(Assassino Júnior) Força o alvo a errar sua próxima ação.")
    @check_game_phase(["night"])
    @check_player_state()
    @check_role([AssassinoJunior])
    @option("jogador", description="O alvo da sua confusão.", autocomplete=search_alive_players)
    async def confundir(self, ctx: ApplicationContext, jogador: str):
        game = ctx.game
        target_member = find_player_by_name(game, jogador)
        if not target_member: await ctx.respond(f"Não achei o jogador vivo '{jogador}'.", ephemeral=True); return
        if target_member.id == ctx.author.id: await ctx.respond("Confundir a si mesmo não é uma boa ideia.", ephemeral=True); return
        if game.last_confused_target.get(ctx.author.id) == target_member.id: await ctx.respond("Você já confundiu essa pessoa na noite passada.", ephemeral=True); return
        player_state = game.get_player_state_by_id(ctx.author.id)
        record_night_action(game, ctx.author.id, player_state.role, "confuse", target_member.id, priority=16)
        game.last_confused_target[ctx.author.id] = target_member.id
        await ctx.respond(f"Você semeia a confusão na mente de **{target_member.display_name}**.", ephemeral=True)

    @commands.slash_command(name="eliminar", description="(Vilões/Bruxo) Escolha um jogador para tentar eliminar.")
    @check_game_phase(["night"])
    @check_player_state()
    @check_role([AssassinoAlfa, AssassinoJunior, Cumplice, Bruxo, AssassinoSimples])
    @option("jogador", description="O jogador que você quer eliminar.", autocomplete=search_alive_players)
    async def eliminar(self, ctx: ApplicationContext, jogador: str):
        game = ctx.game
        player_state = game.get_player_state_by_id(ctx.author.id)
        is_witch = isinstance(player_state.role, Bruxo)
        if is_witch and game.witch_potion_used: await ctx.respond("Sua única poção já foi usada.", ephemeral=True); return
        target_member = find_player_by_name(game, jogador)
        if not target_member: await ctx.respond(f"Não achei o jogador '{jogador}'.", ephemeral=True); return
        if target_member.id == ctx.author.id: await ctx.respond("Se auto-eliminar? Má ideia...", ephemeral=True); return
        if is_witch: game.bruxo_major_action = {"action": "kill", "target_id": target_member.id}
        action_name = "villain_vote" if not is_witch else "witch_kill"
        priority = 30 if not is_witch else 25
        record_night_action(game, ctx.author.id, player_state.role, action_name, target_member.id, priority=priority)
        await ctx.respond(f"Alvo marcado! {target_member.display_name} está na sua mira.", ephemeral=True)

    @commands.slash_command(name="reviver", description="(Anjo/Bruxo) Traga um jogador morto de volta à vida.")
    @check_game_phase(["night"])
    @check_player_state()
    @check_role([Anjo, Bruxo])
    @option("jogador", description="O jogador morto que você quer reviver.", autocomplete=search_dead_players)
    async def reviver(self, ctx: ApplicationContext, jogador: str):
        game = ctx.game
        player_state = game.get_player_state_by_id(ctx.author.id)
        is_angel = isinstance(player_state.role, Anjo)
        is_witch = isinstance(player_state.role, Bruxo)
        if is_angel and game.angel_revive_used: await ctx.respond("Seu milagre já foi usado.", ephemeral=True); return
        if is_witch and game.witch_potion_used: await ctx.respond("Sua única poção já foi usada.", ephemeral=True); return
        target_member = find_dead_player_by_name(game, jogador)
        if not target_member: await ctx.respond(f"Não achei o jogador morto '{jogador}'.", ephemeral=True); return
        target_state = game.get_player_state_by_id(target_member.id)
        if target_state.is_ghost and not isinstance(target_state.role, Prefeito):
            await ctx.respond("A alma deste jogador não pode ser revivda.", ephemeral=True); return
        if target_member.id in game.night_revive_targets:
            await ctx.respond("Alguém já está tentando reviver essa pessoa.", ephemeral=True); return
        if is_witch: game.bruxo_major_action = {"action": "revive", "target_id": target_member.id}
        game.night_revive_targets.append(target_member.id)
        action_name = "angel_revive" if is_angel else "witch_revive"
        record_night_action(game, ctx.author.id, player_state.role, action_name, target_member.id, priority=40)
        await ctx.respond(f"Você tentará trazer {target_member.display_name} de volta do além.", ephemeral=True)

    @commands.slash_command(name="marcar", description="(Detetive) Marque um ou dois jogadores para investigar.")
    @check_game_phase(["night"])
    @check_player_state()
    @check_role([Detetive])
    @option("jogador1", description="O nome do jogador a marcar.", autocomplete=search_alive_players)
    @option("jogador2", description="Opcional. Obrigatório em jogos com mais de 5 pessoas.", autocomplete=search_alive_players, required=False)
    async def marcar(self, ctx: ApplicationContext, jogador1: str, jogador2: str = None):
        game = ctx.game
        num_players = len(game.players)
        player_state = game.get_player_state_by_id(ctx.author.id)
        if num_players <= 5:
            if jogador2 is not None: await ctx.respond("Em partidas pequenas, só pode investigar uma pessoa.", ephemeral=True); return
            target1 = find_player_by_name(game, jogador1)
            if not target1: await ctx.respond(f"Não encontrei o jogador '{jogador1}'.", ephemeral=True); return
            record_night_action(game, ctx.author.id, player_state.role, "mark_detective", priority=60, target1_id=target1.id, target2_id=None)
            await ctx.respond(f"Você está de olho em {target1.display_name} esta noite.", ephemeral=True)
        else:
            if jogador2 is None: await ctx.respond("Em partidas com mais de 5 jogadores, marque duas pessoas.", ephemeral=True); return
            target1 = find_player_by_name(game, jogador1)
            target2 = find_player_by_name(game, jogador2)
            if not target1 or not target2: await ctx.respond(f"Não encontrei '{jogador1}' ou '{jogador2}'.", ephemeral=True); return
            if target1 == target2: await ctx.respond("Escolha dois jogadores diferentes!", ephemeral=True); return
            record_night_action(game, ctx.author.id, player_state.role, "mark_detective", priority=60, target1_id=target1.id, target2_id=target2.id)
            await ctx.respond(f"Você está de olho em {target1.display_name} e {target2.display_name} esta noite.", ephemeral=True)

    @commands.slash_command(name="escolher_alvo", description="(Cúmplice/Júnior/Fofoqueiro/Praga) Escolha seu alvo inicial (Noite 1).")
    @check_game_phase(["night"])
    @check_player_state()
    @check_role([Cumplice, AssassinoJunior, Fofoqueiro, Praga])
    @option("jogador", description="O jogador que você quer escolher como alvo.", autocomplete=search_alive_players)
    async def escolher_alvo(self, ctx: ApplicationContext, jogador: str):
        game = ctx.game
        if game.current_night != 1: await ctx.respond("Essa escolha só pode ser feita na primeira noite!", ephemeral=True); return
        target_member = find_player_by_name(game, jogador)
        if not target_member: await ctx.respond(f"Não achei o jogador '{jogador}'.", ephemeral=True); return
        if target_member.id == ctx.author.id: await ctx.respond("Escolher a si mesmo não é permitido.", ephemeral=True); return
        player_state = game.get_player_state_by_id(ctx.author.id)
        if isinstance(player_state.role, Cumplice):
            target_state = game.get_player_state_by_id(target_member.id)
            info_message = f"Investigação concluída! O papel de {target_member.display_name} é **{target_state.role.name}**."
            await ctx.respond(info_message, ephemeral=True)
            all_villains = [p for p in game.players.values() if p.role and p.role.faction == "Vilões" and p.member.id != ctx.author.id]
            for villain_state in all_villains:
                await send_dm_safe(villain_state.member, f"🤫 O Cúmplice descobriu: {target_member.display_name} é **{target_state.role.name}**.")
            game.accomplice_target_info = {'target_id': target_member.id, 'night': game.current_night}
            return
        if isinstance(player_state.role, Praga):
            game.plague_patient_zero_id = target_member.id
            target_state = game.get_player_state_by_id(target_member.id)
            if target_state: target_state.is_infected = True
        elif isinstance(player_state.role, AssassinoJunior): game.junior_marked_target_id = target_member.id
        elif isinstance(player_state.role, Fofoqueiro): game.fofoqueiro_marked_target_id = target_member.id
        action_name = f"{player_state.role.name.lower().replace(' ', '_')}_target"
        record_night_action(game, ctx.author.id, player_state.role, action_name, target_member.id, priority=70)
        await ctx.respond(f"Alvo definido! Você escolheu {target_member.display_name}.", ephemeral=True)

    @commands.slash_command(name="investigar_aura", description="(Vidente de Aura) Investiga a facção de um jogador.")
    @check_game_phase(["night"])
    @check_player_state()
    @check_role([VidenteDeAura])
    @option("jogador", description="O jogador que você quer investigar.", autocomplete=search_alive_players)
    async def investigar_aura(self, ctx: ApplicationContext, jogador: str):
        game = ctx.game
        target_member = find_player_by_name(game, jogador)
        if not target_member: await ctx.respond(f"Não achei o jogador '{jogador}'.", ephemeral=True); return
        if target_member.id == ctx.author.id: await ctx.respond("Você já sabe a sua própria aura.", ephemeral=True); return
        target_state = game.get_player_state_by_id(target_member.id)
        aura_result = "da Cidade" if target_state.role.faction == "Cidade" else "Não é da Cidade"
        await ctx.respond(f"A aura de {target_member.display_name} **{aura_result}**.", ephemeral=True)

    @commands.slash_command(name="mediunidade", description="(Médium) Converte um jogador morto em um Fantasma aliado.")
    @check_game_phase(["night"])
    @check_player_state()
    @check_role([Medium])
    @option("jogador_morto", description="O espírito que você quer converter.", autocomplete=search_dead_players)
    async def mediunidade(self, ctx: ApplicationContext, jogador_morto: str):
        game = ctx.game
        if game.medium_talk_used: await ctx.respond("Você já usou seu poder de converter um Fantasma.", ephemeral=True); return
        target_member = find_dead_player_by_name(game, jogador_morto)
        if not target_member: await ctx.respond(f"Não encontrei o espírito '{jogador_morto}'.", ephemeral=True); return
        target_state = game.get_player_state_by_id(target_member.id)
        if target_state.is_ghost: await ctx.respond("Este espírito já está ligado a este plano.", ephemeral=True); return
        game.medium_talk_used = True
        target_state.is_ghost = True
        target_state.ghost_master_id = ctx.author.id
        await ctx.respond(f"Você estabeleceu uma conexão com o espírito de **{target_member.display_name}**!", ephemeral=True)
        ghost_embed = discord.Embed(title="👻 Você se tornou um Fantasma! 👻", description=f"O Médium **{ctx.author.display_name}** o trouxe de volta. Use `/assombrar` para vigiar alguém.", color=discord.Color.light_grey())
        await send_dm_safe(target_member, embed=ghost_embed)

    @commands.slash_command(name="assombrar", description="(Fantasma) Escolha um jogador para vigiar esta noite.")
    @check_game_phase(["night"])
    @check_player_state(requires_alive=False)
    @check_is_ghost()
    @option("jogador", description="O alvo da sua assombração.", autocomplete=search_alive_players)
    async def assombrar(self, ctx: ApplicationContext, jogador: str):
        game = ctx.game
        target_member = find_player_by_name(game, jogador)
        if not target_member: await ctx.respond(f"Não achei o jogador '{jogador}'.", ephemeral=True); return
        player_state = game.get_player_state_by_id(ctx.author.id)
        record_night_action(game, ctx.author.id, player_state.role, "haunt", target_member.id, priority=5)
        await ctx.respond(f"Você focará sua energia espectral em **{target_member.display_name}** esta noite.", ephemeral=True)
        await send_dm_safe(target_member, f"Você sente um arrepio... O fantasma de **{ctx.author.display_name}** está te assombrando. 👻")

    @commands.slash_command(name="exterminar", description="(Praga) Libera a praga para eliminar todos os infectados.")
    @check_game_phase(["night"])
    @check_player_state()
    @check_role([Praga])
    async def exterminar(self, ctx: ApplicationContext):
        game = ctx.game
        if game.plague_exterminate_used: await ctx.respond("Você já tentou o extermínio uma vez.", ephemeral=True); return
        player_state = game.get_player_state_by_id(ctx.author.id)
        record_night_action(game, ctx.author.id, player_state.role, "plague_exterminate", priority=35)
        await ctx.respond("☣️ Você decidiu que é a hora! Você liberará o poder total da praga!", ephemeral=True)

    @commands.slash_command(name="disparar", description="(Xerife) Atira em um jogador durante o dia.")
    @check_game_phase(["day_discussion", "day_voting"])
    @check_player_state(requires_dm=False)
    @check_role([Xerife])
    @option("jogador", description="O jogador em quem você quer atirar.", autocomplete=search_alive_players)
    async def disparar(self, ctx: ApplicationContext, jogador: str):
        game = ctx.game
        game_flow_cog = self.bot.get_cog("GameFlowCog")
        player_state = game.get_player_state_by_id(ctx.author.id)
        target_member = find_player_by_name(game, jogador)
        if player_state.is_confused:
            await ctx.respond("😵‍💫 **Que tontura!** Sua ação saiu errada.", ephemeral=True)
            possible_targets = [p.member for p in game.get_alive_players_states() if p.member.id != ctx.author.id]
            if not possible_targets: return
            target_member = random.choice(possible_targets)
        if game.sheriff_shot_this_day: await ctx.respond("Você só pode disparar uma vez por dia.", ephemeral=True); return
        max_shots = 1 if len(game.players) <= 6 else 2
        if game.sheriff_shots_fired >= max_shots: await ctx.respond(f"Você já gastou suas {max_shots} balas.", ephemeral=True); return
        if not target_member: await ctx.respond(f"Não achei o jogador '{jogador}'.", ephemeral=True); return
        if target_member.id == ctx.author.id: await ctx.respond("Atirar em si mesmo não é boa ideia.", ephemeral=True); return
        if game_flow_cog: await asyncio.create_task(game_flow_cog.play_sound_effect(game, "SHERIFF_SHOT"))
        if not player_state.is_confused:
            await ctx.respond("Disparo efetuado!", ephemeral=True)
        await send_public_message(self.bot, game.text_channel, f"**BANG!** 💥 {ctx.author.mention}, o Xerife, atira em {target_member.mention}!", allowed_mentions=discord.AllowedMentions(users=True))
        game.sheriff_shots_fired += 1
        game.sheriff_shot_this_day = True
        if not game.sheriff_revealed:
            game.sheriff_revealed = True
            await send_public_message(self.bot, game.text_channel, f"🚨 {ctx.author.mention} se revelou como o **Xerife**! ⭐")
        target_role = game.get_player_state_by_id(target_member.id).role
        if isinstance(target_role, AssassinoAlfa):
            winners = [p.member for p in game.players.values() if p.role.faction == "Cidade"]
            await game_flow_cog.end_game(game, "Vitória da Cidade!", winners, "Cidade", "O Xerife eliminou o Assassino Alfa!", sound_event_key="SHERIFF_WIN")
            return
        if isinstance(target_role, Prefeito):
            winners = [p.member for p in game.players.values() if p.role.faction == "Vilões"]
            await game_flow_cog.end_game(game, "Vitória dos Vilões!", winners, "Vilões", "O Xerife eliminou o Prefeito!", sound_event_key="VILLAINS_WIN")
            return
        if game_flow_cog:
            game.killers[target_member.id] = ctx.author.id
            await game_flow_cog.process_death(game, target_member, "shot_by_sheriff")
            await game_flow_cog.check_game_end(game, f" após o disparo do Xerife em {target_member.display_name}")

    @commands.slash_command(name="votar", description="Vote em quem você acha que deve ser linchado.")
    @check_game_phase(["day_voting"])
    @check_player_state(requires_dm=True)
    @check_role([])
    @option("jogador", description="O jogador em quem você quer votar.", autocomplete=search_alive_players)
    async def votar(self, ctx: ApplicationContext, jogador: str):
        game = ctx.game
        target_member = find_player_by_name(game, jogador)
        if not target_member: await ctx.respond(f"Não achei o jogador '{jogador}'.", ephemeral=True); return
        
        # Garante que o voto para pular seja removido se o jogador decidir votar em alguém.
        if ctx.author.id in game.day_skip_votes:
            game.day_skip_votes.remove(ctx.author.id)
            
        game.day_votes[ctx.author.id] = target_member.id
        await ctx.respond(f"Seu voto em {target_member.display_name} foi registrado!", ephemeral=True)

    @commands.slash_command(name="pular", description="Vote para pular a votação do dia.")
    @check_game_phase(["day_voting"])
    @check_player_state()
    @check_role([])
    async def pular(self, ctx: ApplicationContext):
        game = ctx.game
        player_id = ctx.author.id
        if player_id in game.day_votes: del game.day_votes[player_id]
        game.day_skip_votes.add(player_id)
        await ctx.respond("Seu voto para pular foi registrado!", ephemeral=True)
        num_alive = len(game.get_alive_players())
        majority_needed = (num_alive // 2) + 1
        if len(game.day_skip_votes) >= majority_needed:
            game_flow_cog = self.bot.get_cog("GameFlowCog")
            if game_flow_cog:
                if game.current_timer_task and not game.current_timer_task.done():
                    game.current_timer_task.cancel()
                await game_flow_cog.end_day_voting(game)

    # --- Funções de Resolução ---

    async def process_lynch(self, game: GameInstance) -> Dict[str, Any]:
        results = {"public_messages": [], "game_over": False, "sound_event": None}
        game_flow_cog = self.bot.get_cog("GameFlowCog")
        num_alive = len(game.get_alive_players())
        majority_needed = (num_alive // 2) + 1
        if len(game.day_skip_votes) >= majority_needed:
            results["public_messages"].append("A maioria decidiu pular a votação.")
            return results
        votes = game.day_votes
        if not votes:
            results["public_messages"].append("Ninguém foi linchado."); return results
        if game.fraud_active:
            logger.info(f"[Jogo #{game.text_channel.id}] FRAUDE ATIVADA! Embaralhando votos...")
            voter_ids, target_ids = list(votes.keys()), list(votes.values())
            random.shuffle(target_ids)
            votes = {voter: target for voter, target in zip(voter_ids, target_ids)}
            results["public_messages"].append("Os resultados da votação parecem... estranhos.")
        vote_counts = {}
        for voter_id, target_id in votes.items():
            weight = 1
            if game.decreto_active:
                voter_state = game.get_player_state_by_id(voter_id)
                if voter_state and voter_state.role:
                    if isinstance(voter_state.role, Prefeito): weight = 3
                    elif voter_state.role.faction == "Cidade": weight = 2
            vote_counts[target_id] = vote_counts.get(target_id, 0) + weight
        if game.decreto_active:
            vote_details = [f"{game.get_player_by_id(pid).display_name} ({count} votos)" for pid, count in vote_counts.items()]
            results["public_messages"].append(f"Com o Decreto, a contagem final foi: {', '.join(vote_details)}.")
        max_votes = max(vote_counts.values()) if vote_counts else 0
        if max_votes < majority_needed:
            results["public_messages"].append(f"A votação não atingiu a maioria de {majority_needed} votos.")
            return results
        lynched_candidates = [pid for pid, count in vote_counts.items() if count == max_votes]
        if len(lynched_candidates) != 1:
            results["public_messages"].append("Houve um empate na votação."); return results
        lynched_player_state = game.get_player_state_by_id(lynched_candidates[0])
        lynched_member = lynched_player_state.member
        if isinstance(lynched_player_state.role, Prefeito) and not game.prefeito_saved_once:
            game.prefeito_saved_once = True
            results["public_messages"].append(f"A votação para linchar **{lynched_member.display_name}** foi esmagadora! No entanto, a cidade reconsiderou."); return results
        results["public_messages"].append(f"Com {max_votes} votos, **{lynched_member.display_name}** foi linchado!")
        if game_flow_cog: await game_flow_cog.process_death(game, lynched_member, "lynched")
        if isinstance(lynched_player_state.role, Palhaco):
            results["sound_event"] = "CLOWN_WIN"
            end_game_args = { "title": "Vitória do Palhaço!", "winners": [lynched_member], "faction": "Solo (Palhaço)", "reason": f"{lynched_member.display_name} conseguiu ser linchado!", "sound_event_key": "CLOWN_WIN" }
            if game_flow_cog: await game_flow_cog.end_game(game, **end_game_args)
            results.update({"game_over": True})
        else:
            results["sound_event"] = "PLAYER_DEATH"
        return results

    # --- MÉTODOS PRIVADOS DE RESOLUÇÃO NOTURNA (REFATORADOS) ---

    async def _apply_status_effects(self, game: GameInstance, sorted_actions: List[Any], results: Dict):
        """Aplica efeitos que precisam ser resolvidos primeiro, como confusão e corrupção."""
        for player_id, action_data in sorted_actions:
            action_name = action_data["action"]
            target_id = action_data.get("target_id")
            
            # Confusão é aplicada primeiro para afetar outras ações
            if action_name == "confuse":
                if target_state := game.get_player_state_by_id(target_id):
                    target_state.is_confused = True

        # Aplica o efeito da confusão nas ações registradas
        for player_id, action_data in game.night_actions.items():
            if (player_state := game.get_player_state_by_id(player_id)) and player_state.is_confused and "target_id" in action_data:
                original_target_id = action_data["target_id"]
                is_revive = action_data["action"] in ["angel_revive", "witch_revive"]
                
                possible_targets_query = [p.member.id for p in game.players.values() if not p.is_alive] if is_revive else [p.member.id for p in game.get_alive_players_states()]
                possible_targets = [pid for pid in possible_targets_query if pid not in [player_id, original_target_id]]
                
                if possible_targets:
                    new_target_id = random.choice(possible_targets)
                    action_data["target_id"] = new_target_id
                    logger.info(f"Ação de {player_state.member.display_name} confundida! Novo alvo: {game.get_player_by_id(new_target_id).display_name}")
                    results["dm_messages"].setdefault(player_id, []).append("😵‍💫 **Que tontura!** Sua ação saiu toda errada.")

        # Aplica corrupção e proteção
        for player_id, action_data in sorted_actions:
            action_name = action_data["action"]
            target_id = action_data.get("target_id")
            
            if action_name == "corrupt":
                if target_state := game.get_player_state_by_id(target_id):
                    target_state.is_corrupted = True
                    results["dm_messages"].setdefault(target_id, []).append("😵‍💫 Sua mente foi invadida! Você não consegue usar sua habilidade esta noite.")
            
            elif action_name == "protect":
                protector_state = game.get_player_state_by_id(player_id)
                if protector_state and not protector_state.is_corrupted:
                    if target_state := game.get_player_state_by_id(target_id):
                        target_state.protected_by = player_id

    async def _resolve_unique_actions(self, game: GameInstance, sorted_actions: List[Any], results: Dict):
        """Resolve ações únicas como Possessão, Cupido e outras que alteram o estado do jogo."""
        for player_id, action_data in sorted_actions:
            action_name = action_data["action"]
            target_id = action_data.get("target_id")
            player_state = game.get_player_state_by_id(player_id)
            if not player_state or player_state.is_corrupted: continue

            if action_name == "possess":
                game.skip_villain_kill = True
                if target_state := game.get_player_state_by_id(target_id):
                    target_state.possession_points += 1
                    results["dm_messages"].setdefault(player_id, []).append(f"Você adicionou +1 ponto de possessão a {target_state.member.display_name}. Total: {target_state.possession_points}/3.")
                    if target_state.possession_points >= 3:
                        target_state.role = AssassinoSimples()
                        await send_dm_safe(target_state.member, f"Sua mente foi quebrada! Você agora é um **Assassino Simples**.")
                        all_villains = [p.member.display_name for p in game.players.values() if p.role.faction == "Vilões" and p.is_alive]
                        await send_dm_safe(target_state.member, f"Seus novos companheiros são: **{', '.join(all_villains)}**")
                        for p_state in game.players.values():
                            if p_state.role.faction == "Vilões" and p_state.is_alive and p_state.member.id != target_id:
                                await send_dm_safe(p_state.member, f"**{target_state.member.display_name}** foi corrompido e agora é um Assassino Simples.")
            
            elif action_name == "cupid_match":
                lover1_id, lover2_id = action_data["lover1_id"], action_data["lover2_id"]
                game.lovers = (lover1_id, lover2_id)
                if (lover1 := game.get_player_by_id(lover1_id)) and (lover2 := game.get_player_by_id(lover2_id)):
                    dm_msg1 = f"💘 O Cupido acertou você! Seu grande amor é **{lover2.display_name}**. Se um de vocês morrer, o outro morrerá junto."
                    dm_msg2 = f"💘 O Cupido acertou você! Seu grande amor é **{lover1.display_name}**. Se um de vocês morrer, o outro morrerá junto."
                    results["dm_messages"].setdefault(lover1_id, []).append(dm_msg1)
                    results["dm_messages"].setdefault(lover2_id, []).append(dm_msg2)

    def _gather_kill_attempts(self, game: GameInstance, sorted_actions: List[Any]) -> Dict[int, List[tuple]]:
        """Coleta todos os votos e tentativas de assassinato da noite."""
        kill_attempts = {}
        villain_votes = {}
        for player_id, action_data in sorted_actions:
            player_state = game.get_player_state_by_id(player_id)
            if not player_state or player_state.is_corrupted: continue
            
            action = action_data["action"]
            if action == "villain_vote":
                weight = 2 if isinstance(action_data["role"], AssassinoAlfa) else 1
                villain_votes[action_data["target_id"]] = villain_votes.get(action_data["target_id"], 0) + weight
            elif action == "witch_kill":
                kill_attempts.setdefault(action_data["target_id"], []).append(("witch", player_id))
                game.witch_potion_used = True
        
        if villain_votes and not game.skip_villain_kill:
            target_id = max(villain_votes, key=villain_votes.get)
            voters = [p_id for p_id, action in game.night_actions.items() if action.get("action") == "villain_vote" and action.get("target_id") == target_id]
            kill_attempts.setdefault(target_id, []).append(("villain", voters))
        
        return kill_attempts

    def _resolve_deaths(self, game: GameInstance, kill_attempts: Dict, results: Dict) -> List[tuple]:
        """Processa as tentativas de morte, considerando proteções, e retorna quem morreu."""
        final_deaths = []
        for target_id, killers_info in kill_attempts.items():
            target_state = game.get_player_state_by_id(target_id)
            if not target_state or not target_state.is_alive: continue
            
            attack_source, attacker_id = killers_info[0]
            
            # A proteção funciona contra o ataque dos vilões, mas não contra o ataque da bruxa.
            if target_state.protected_by and attack_source == 'villain':
                protector_state = game.get_player_state_by_id(target_state.protected_by)
                if protector_state:
                    if not protector_state.bodyguard_vest_used:
                        protector_state.bodyguard_vest_used = True
                        results["sound_events"].append("PROTECTION_SUCCESS")
                        results["dm_messages"].setdefault(protector_state.member.id, []).append("🛡️ Você protegeu seu alvo de um ataque e sobreviveu!")
                    else:
                        final_deaths.append((protector_state.member.id, "bodyguard_sacrifice", target_id))
                        results["sound_events"].append("PLAYER_DEATH")
                        results["public_messages"].append(f"🛡️ O **Guarda-Costas** foi encontrado morto no lugar de {target_state.member.display_name}!")
                continue
            
            if isinstance(target_state.role, GuardaCostas) and not target_state.bodyguard_vest_used:
                target_state.bodyguard_vest_used = True
                results["sound_events"].append("PROTECTION_SUCCESS")
                results["dm_messages"].setdefault(target_id, []).append("🛡️ Você foi atacado, mas sua resistência o salvou!")
                continue
            
            final_deaths.append((target_id, attack_source, attacker_id))
        return final_deaths

    async def _resolve_revivals(self, game: GameInstance, sorted_actions: List[Any], final_deaths: List[tuple], results: Dict) -> List[tuple]:
        """Processa as tentativas de reviver e retorna quem foi revivido."""
        revived_this_night = []
        for player_id, action_data in sorted_actions:
            player_state = game.get_player_state_by_id(player_id)
            if not player_state or player_state.is_corrupted: continue
            
            action = action_data["action"]
            if action in ["angel_revive", "witch_revive"]:
                target_id = action_data["target_id"]
                target_state = game.get_player_state_by_id(target_id)
                
                if target_state and not target_state.is_alive and not any(d[0] == target_id for d in final_deaths):
                    target_state.revive()
                    revived_this_night.append((target_id, player_id))
                    results["sound_events"].append("PLAYER_REVIVE")
                    
                    if action == "witch_revive": game.witch_potion_used = True
                    if action == "angel_revive": game.angel_revive_used = True
                    game.reset_flags_for_player(target_id)
                    
                    if isinstance(target_state.role, Prefeito) and target_state.ghost_master_id:
                        if medium_state := game.get_player_state_by_id(target_state.ghost_master_id):
                            game.medium_talk_used = False
                            await send_dm_safe(medium_state.member, "O Prefeito foi revivido! Seu poder foi restaurado.")
        return revived_this_night

    async def _resolve_information_and_plague(self, game: GameInstance, sorted_actions: List[Any], final_deaths: List[tuple], night_visits: Dict, results: Dict):
        """Resolve ações de informação (Detetive, Fantasma) e a lógica da Praga."""
        for player_id, action_data in sorted_actions:
            p_state = game.get_player_state_by_id(player_id)
            if not p_state or p_state.is_corrupted: continue
            
            if action_data["action"] == "mark_detective":
                killed_this_night_ids = [d[0] for d in final_deaths]
                marked_ids = [action_data.get("target1_id"), action_data.get("target2_id")]
                marked_killed_ids = [tid for tid in marked_ids if tid in killed_this_night_ids and tid is not None]
                
                if not marked_killed_ids:
                    results["dm_messages"].setdefault(player_id, []).append("🕵️ Sua vigília foi tranquila. Nenhum dos seus alvos morreu.")
                else:
                    killed_id = marked_killed_ids[0]
                    death_info = next((d for d in final_deaths if d[0] == killed_id), None)
                    if (killed_member := game.get_player_by_id(killed_id)) and death_info:
                        _, _, killer_info = death_info
                        killer_ids = killer_info if isinstance(killer_info, list) else [killer_info]
                        if killer_ids and (killer_member := game.get_player_by_id(random.choice(killer_ids))):
                            innocent_pool = [p.member for p in game.get_alive_players_states() if p.member.id not in [player_id, killed_id, killer_member.id]]
                            clue_members = [killer_member, random.choice(innocent_pool)] if innocent_pool else [killer_member]
                            random.shuffle(clue_members)
                            info_msg = f"🕵️ {killed_member.display_name} foi morto. Um destes está envolvido: **{', '.join([m.display_name for m in clue_members])}**."
                        else: info_msg = f"🕵️ {killed_member.display_name} foi morto, mas o assassino é um mistério."
                        results["dm_messages"].setdefault(player_id, []).append(info_msg)

        haunt_action = next((data for _, data in sorted_actions if data["action"] == "haunt"), None)
        if haunt_action:
            haunt_target_id, ghost_id = haunt_action["target_id"], haunt_action["player_id"]
            if (ghost_state := game.get_player_state_by_id(ghost_id)) and ghost_state.ghost_master_id:
                medium_id = ghost_state.ghost_master_id
                visits = night_visits.get(haunt_target_id, {'visited_by': set(), 'visited': set()})
                visited_by_names = [game.get_player_by_id(pid).display_name for pid in visits['visited_by'] if pid != ghost_id]
                visited_names = [game.get_player_by_id(pid).display_name for pid in visits['visited']]
                report = (f"Relatório da Assombração sobre **{game.get_player_by_id(haunt_target_id).display_name}**:\n"
                          f"- Foi visitado por: **{', '.join(visited_by_names) if visited_by_names else 'Ninguém'}**\n"
                          f"- Visitou: **{', '.join(visited_names) if visited_names else 'Ninguém'}**")
                results["dm_messages"].setdefault(ghost_id, []).append(report)
                results["dm_messages"].setdefault(medium_id, []).append(report)

        prague_exterminate_action = next((data for _, data in sorted_actions if data["action"] == "plague_exterminate"), None)
        if prague_exterminate_action and not game.plague_exterminate_used:
            game.plague_exterminate_used = True
            infected_to_die_ids = {pid for pid, pstate in game.players.items() if pstate.is_infected and pstate.is_alive}
            if len(infected_to_die_ids) >= 4 and (praga_member := game.get_player_by_id(prague_exterminate_action["player_id"])) and (game_flow_cog := self.bot.get_cog("GameFlowCog")):
                end_args = {"title": "Vitória da Praga!", "winners": [praga_member], "faction": "Solo (Praga)", "reason": f"A Praga eliminou {len(infected_to_die_ids)} jogadores!", "sound_event_key": "PLAGUE_WIN"}
                await game_flow_cog.end_game(game, **end_args); results["game_over"] = True
                return
            if infected_to_die_ids:
                results["plague_kill_count"] = len(infected_to_die_ids)
                for infected_id in infected_to_die_ids:
                    final_deaths.append((infected_id, "killed_by_plague", prague_exterminate_action["player_id"]))

        if game.plague_patient_zero_id and (pz_state := game.get_player_state_by_id(game.plague_patient_zero_id)) and pz_state.is_alive:
            newly_infected = []
            def infect_player(player_id):
                if player_id != game.plague_player_id and (p_state := game.get_player_state_by_id(player_id)) and not p_state.is_infected:
                    p_state.is_infected = True; newly_infected.append(player_id)
            for interactor_id, act_data in game.night_actions.items():
                if act_data.get("target_id") == game.plague_patient_zero_id: infect_player(interactor_id)
            if (pz_action := game.night_actions.get(game.plague_patient_zero_id)) and (target_id := pz_action.get("target_id")): infect_player(target_id)
            if newly_infected:
                for infected_id in newly_infected:
                    results["dm_messages"].setdefault(infected_id, []).append("🤒 Você se sente febril... Você foi infectado pela Praga!")

    # --- MÉTODO PRINCIPAL DE RESOLUÇÃO (ORQUESTRADOR) ---

    async def resolve_night_actions(self, game: GameInstance) -> Dict[str, Any]:
        logger.info(f"[Jogo #{game.text_channel.id}] --- Resolvendo Ações Noturnas ---")
        results = {"killed_players": [], "revived_players": [], "sound_events": [], "plague_kill_count": 0, "dm_messages": {}, "public_messages": [], "game_over": False}
        for p_state in game.players.values(): results["dm_messages"][p_state.member.id] = []
        
        sorted_actions = sorted(game.night_actions.items(), key=lambda item: item[1]["priority"])
        night_visits = {p_id: {'visited_by': set(), 'visited': set()} for p_id in game.players}
        for player_id, action_data in sorted_actions:
            if target_id := action_data.get("target_id"):
                night_visits[target_id]['visited_by'].add(player_id)
                night_visits[player_id]['visited'].add(target_id)

        await self._apply_status_effects(game, sorted_actions, results)
        await self._resolve_unique_actions(game, sorted_actions, results)
        if results.get("game_over"): return results
        
        kill_attempts = self._gather_kill_attempts(game, sorted_actions)
        deaths_before_revive = self._resolve_deaths(game, kill_attempts, results)
        revived_this_night = await self._resolve_revivals(game, sorted_actions, deaths_before_revive, results)
        
        final_deaths = [d for d in deaths_before_revive if d[0] not in [r[0] for r in revived_this_night]]
        
        await self._resolve_information_and_plague(game, sorted_actions, final_deaths, night_visits, results)
        if results.get("game_over"): return results
        
        if final_deaths: results["killed_players"] = final_deaths
        if revived_this_night: results["revived_players"] = revived_this_night
        
        game.clear_nightly_states()
        
        logger.info(f"[Jogo #{game.text_channel.id}] --- Resolução Noturna Concluída ---")
        return results

def setup(bot: commands.Bot):
    bot.add_cog(ActionsCog(bot))