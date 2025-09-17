# cogs/game_flow.py

import discord
from discord.ext import commands
import logging
import asyncio
import os
import random
from typing import List, Dict, Any, Optional

import config
from .game_instance import GameInstance
from .utils import send_public_message, get_random_humor, send_dm_safe
from roles.solo_roles import Praga, Cupido, Corruptor, Palhaco, Bruxo, Fofoqueiro, CacadorDeCabecas
from roles.viloes_roles import AssassinoAlfa, AssassinoJunior, Cumplice
from roles.cidade_roles import Prefeito, Xerife, Anjo, Medium, VidenteDeAura, GuardaCostas, CidadaoComum
from roles.base_role import Role

logger = logging.getLogger(__name__)

# --- Classes de View para Interação (sem mudanças) ---

class ShowdownSelect(discord.ui.Select):
    def __init__(self, user_to_act: discord.Member, targets: List[discord.Member]):
        self.user_to_act = user_to_act
        options = [discord.SelectOption(label=member.display_name, value=str(member.id)) for member in targets]
        super().__init__(placeholder=f"Escolha seu alvo, {user_to_act.display_name}...", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_to_act.id:
            await interaction.response.send_message("Você não é a pessoa que deve agir agora!", ephemeral=True)
            return
        target_id = int(self.values[0])
        self.view.result = target_id
        self.disabled = True
        self.placeholder = f"Você escolheu seu alvo."
        await interaction.message.edit(view=self.view)
        self.view.stop()
        await interaction.response.defer()

class ShowdownView(discord.ui.View):
    def __init__(self, user_to_act: discord.Member, targets: List[discord.Member], timeout=60.0):
        super().__init__(timeout=timeout)
        self.result: Optional[int] = None
        self.add_item(ShowdownSelect(user_to_act, targets))
    async def on_timeout(self):
        self.stop()

class GameFlowCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        logger.info("Cog GameFlow carregado.")

    @commands.Cog.listener()
    async def on_ready(self):
        logger.info("GameFlowCog está pronto para orquestrar as partidas.")

    def cog_unload(self):
        for game in self.bot.game_manager.games.values():
            if game.current_timer_task and not game.current_timer_task.done():
                game.current_timer_task.cancel()

    async def _update_voice_permissions(self, game: GameInstance, mute: bool, force_unmute_all: bool = False):
        if not game.voice_channel: return
        logger.info(f"[Jogo #{game.text_channel.id}] Atualizando permissões de voz. Mute Geral = {mute}")
        tasks = []
        for member in game.voice_channel.members:
            if member.bot: continue
            should_be_muted = mute
            if force_unmute_all: should_be_muted = False
            else:
                if player_state := game.get_player_state_by_id(member.id):
                    should_be_muted = mute or not player_state.is_alive
            tasks.append(self._set_member_mute(member, should_be_muted, "Controle de fase do jogo"))
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _set_member_mute(self, member: discord.Member, mute: bool, reason: str):
        try:
            if member.voice and member.voice.mute != mute:
                await member.edit(mute=mute, reason=reason)
        except Exception as e:
            logger.error(f"Falha ao mutar/desmutar {member.display_name}: {e}")

    def _start_timer(self, game: GameInstance, duration: int, next_phase_func):
        if game.current_timer_task and not game.current_timer_task.done():
            game.current_timer_task.cancel()
        async def timer_task():
            try:
                await asyncio.sleep(duration)
                if self.bot.game_manager.get_game(game.text_channel.id):
                    await next_phase_func(game)
            except asyncio.CancelledError:
                logger.info(f"[Jogo #{game.text_channel.id}] Timer cancelado.")
        game.current_timer_task = asyncio.create_task(timer_task())

    # Em cogs/game_flow.py

async def play_sound_effect(self, game: GameInstance, event_key: str, wait_for_finish: bool = False):
    if not config.AUDIO_ENABLED or not game.voice_channel:
        return
    if not (sound_list := config.AUDIO_FILES.get(event_key)):
        return
        
    chosen_file = random.choice(sound_list)
    audio_path = os.path.join(config.AUDIO_PATH, chosen_file)
    if not os.path.exists(audio_path):
        # Adiciona um log mais visível para este erro comum
        logger.error(f"Arquivo de áudio não encontrado em: {audio_path}")
        return
    if not game.voice_channel.members:
        return

    voice_client = discord.utils.get(self.bot.voice_clients, guild=game.guild)
    
    try:
        # 1. Garante que estamos conectados e no canal certo
        if voice_client and voice_client.is_connected():
            if voice_client.channel != game.voice_channel:
                await voice_client.move_to(game.voice_channel)
        else:
            # Tenta conectar com um timeout generoso
            voice_client = await asyncio.wait_for(game.voice_channel.connect(), timeout=15.0)

        # 2. Garante que o cliente de voz está pronto antes de tocar
        if not voice_client or not voice_client.is_connected():
            logger.warning(f"[Jogo #{game.text_channel.id}] Não foi possível estabelecer uma conexão de voz.")
            return

        # 3. Toca o áudio de forma segura
        if voice_client.is_playing():
            voice_client.stop()
        
        source = discord.FFmpegPCMAudio(audio_path)
        
        if wait_for_finish:
            finished = asyncio.Event()
            voice_client.play(source, after=lambda e: finished.set())
            # Espera o som terminar ou um timeout para não travar o jogo
            await asyncio.wait_for(finished.wait(), timeout=30.0)
        else:
            voice_client.play(source)

    except asyncio.TimeoutError:
        logger.error(f"[Jogo #{game.text_channel.id}] Timeout ao tentar conectar ou tocar áudio.")
    except Exception as e:
        logger.error(f"[Jogo #{game.text_channel.id}] Erro inesperado ao tocar áudio: {e}", exc_info=True)

    @commands.slash_command(name="iniciar", description="Inicia a primeira noite do jogo neste canal.")
    async def iniciar_jogo(self, ctx: discord.ApplicationContext):
        game = self.bot.game_manager.get_game(ctx.channel.id)
        if not game: await ctx.respond("Não há jogo sendo preparado neste canal.", ephemeral=True); return
        if game.current_phase != "preparing": await ctx.respond("O jogo não está em fase de preparação.", ephemeral=True); return
        if ctx.author != game.game_master: await ctx.respond("Apenas quem usou `/preparar` pode iniciar o jogo!", ephemeral=True); return
        
        await ctx.respond("Que comecem as tretas! A primeira noite está caindo... 🤫", ephemeral=False)
        await self.start_night(game)

    async def start_night(self, game: GameInstance):
        game.current_phase = "night"
        game.current_night += 1
        game.sheriff_shot_this_day = False
        game.death_reasons.clear()
        game.killers.clear()
        logger.info(f"[Jogo #{game.text_channel.id}] --- Iniciando Noite {game.current_night} ---")
        if game.current_night == 1 and (actions_cog := self.bot.get_cog("ActionsCog")):
            await actions_cog.distribute_initial_info(game)
        
        await self._update_voice_permissions(game, mute=True)
        await self.play_sound_effect(game, "NIGHT_START")
        announcement_text = f"🌃 **NOITE {game.current_night}** 🌃\n{get_random_humor('NIGHT_START')}"
        image_path = os.path.join(config.IMAGES_PATH, config.EVENT_IMAGES["NIGHT_START"])
        await send_public_message(self.bot, game.text_channel, message=announcement_text, file_path=image_path)
        self._start_timer(game, config.NIGHT_DURATION_SECONDS, self.end_night)

    async def force_night(self, game: GameInstance):
        if game.current_timer_task and not game.current_timer_task.done():
            game.current_timer_task.cancel()
        await self.start_night(game)

    async def _announce_revival_chance(self, game: GameInstance):
        can_revive_roles = []
        if any(isinstance(p.role, Anjo) for p in game.get_alive_players_states()) and not game.angel_revive_used: can_revive_roles.append("um Anjo")
        if any(isinstance(p.role, Bruxo) for p in game.get_alive_players_states()) and not game.witch_potion_used: can_revive_roles.append("um Bruxo")
        if not can_revive_roles: return
        message = f"🚨 **ALERTA** 🚨\nOs Vilões foram eliminados, mas o Prefeito caiu!\nO destino da cidade está nas mãos de **{' e '.join(can_revive_roles)}**."
        await send_public_message(self.bot, game.text_channel, message=message)

    async def _resolve_pending_endgame(self, game: GameInstance):
        game.pending_resolution = False
        logger.info(f"[Jogo #{game.text_channel.id}] Resolvendo fim de jogo pendente...")
        prefeito_state = next((p for p in game.players.values() if isinstance(p.role, Prefeito)), None)
        if prefeito_state and prefeito_state.is_alive:
            winners = [p.member for p in game.players.values() if p.role.faction == "Cidade"]
            await self.end_game(game, "Vitória da Cidade!", winners, "Cidade", "O milagre aconteceu! O Prefeito foi revivido!")
        else:
            await self.check_seventh_day_win(game, is_resolution=True)

    async def end_night(self, game: GameInstance):
        logger.info(f"[Jogo #{game.text_channel.id}] --- Fim da Noite {game.current_night} ---")
        await send_public_message(self.bot, game.text_channel, "A noite acabou! Processando os eventos...")
        actions_cog = self.bot.get_cog("ActionsCog")
        if not actions_cog: logger.error(f"[Jogo #{game.text_channel.id}] CRÍTICO: ActionsCog não encontrado."); return
        
        alive_before_ids = {p.member.id for p in game.get_alive_players_states()}
        night_results = await actions_cog.resolve_night_actions(game)
        
        if night_results.get("game_over"): return
        if game.pending_resolution: await self._resolve_pending_endgame(game); return
        
        for victim_id, reason, killer_id in night_results.get("killed_players", []):
            if member := game.get_player_by_id(victim_id):
                game.killers[victim_id] = killer_id
                if reason == 'witch': game.successful_major_actions.append({'actor': killer_id, 'action': 'kill', 'target': victim_id})
                await self.process_death(game, member, reason)
                if not self.bot.game_manager.get_game(game.text_channel.id): return
        
        for player_id, messages in night_results.get("dm_messages", {}).items():
            if messages:
                if player := game.get_player_by_id(player_id):
                    await send_dm_safe(player, "\n".join(messages))
        
        for revived_id, reviver_id in night_results.get("revived_players", []):
            game.successful_major_actions.append({'actor': reviver_id, 'action': 'revive', 'target': revived_id})

        if await self.check_game_end(game, "após os eventos da noite"): return
        
        alive_after_ids = {p.member.id for p in game.get_alive_players_states()}
        died_this_night_ids = alive_before_ids - alive_after_ids
        revived_today_ids = alive_after_ids - alive_before_ids
        killed_today = [game.get_player_by_id(pid) for pid in died_this_night_ids if pid]
        revived_today = [game.get_player_by_id(pid) for pid in revived_today_ids if pid]
        day_messages, image_key = [], None
        day_messages.extend(night_results.get("public_messages", []))

        if night_results.get("plague_kill_count", 0) > 0:
            image_key = "PLAGUE_KILL"
            day_messages.append("☣️ A praga se espalhou, deixando um rastro de destruição!")
            if killed_today: day_messages.append(f"Encontramos os corpos de: **{', '.join(sorted([m.display_name for m in killed_today]))}**.")
        elif killed_today:
            image_key = "DAY_DEATH"
            day_messages.append(f"Manhã trágica! Encontramos os corpos de: **{', '.join(sorted([m.display_name for m in killed_today]))}**.")
        if revived_today:
            if not image_key: image_key = "DAY_REVIVAL"
            revived_names = [m.display_name for m in revived_today]
            day_messages.append(f"Milagre! **{', '.join(revived_names)}** {'retornou' if len(revived_names) == 1 else 'retornaram'} dos mortos!")
        
        if not any(killed_today) and not any(revived_today) and not day_messages:
            image_key = "DAY_SAFE"
            day_messages.append("Uma noite calma... Ninguém morreu.")
        
        image_key = image_key or "DAY_DEATH"
        image_path = os.path.join(config.IMAGES_PATH, config.EVENT_IMAGES[image_key])
        await send_public_message(self.bot, game.text_channel, message="\n".join(day_messages), file_path=image_path)
        
        await self.start_day_discussion(game)

    async def start_day_discussion(self, game: GameInstance):
        game.current_phase = "day_discussion"
        game.current_day += 1
        logger.info(f"[Jogo #{game.text_channel.id}] --- Iniciando Dia {game.current_day} ---")
        await self._update_voice_permissions(game, mute=False)
        await self.play_sound_effect(game, "DAY_START")
        await send_public_message(self.bot, game.text_channel, f"☀️ **DIA {game.current_day}** ☀️\n{get_random_humor('DAY_START')}")
        self._start_timer(game, config.DAY_DISCUSSION_DURATION_SECONDS, self.start_day_voting)

    async def start_day_voting(self, game: GameInstance):
        game.current_phase = "day_voting"
        game.clear_daily_states()
        await self.play_sound_effect(game, "VOTE_START")
        await send_public_message(self.bot, game.text_channel, f"⏳ **VOTAÇÃO ABERTA!** ⏳\n{get_random_humor('VOTE_START')}")
        for player_state in game.get_alive_players_states():
            await send_dm_safe(player_state.member, "É hora de apontar o dedo! Use `/votar [nome]` na nossa DM para me dizer quem deve ser linchado.")
        self._start_timer(game, config.VOTE_DURATION_SECONDS, self.end_day_voting)

    async def end_day_voting(self, game: GameInstance):
        logger.info(f"[Jogo #{game.text_channel.id}] --- Fim da Votação ---")
        await send_public_message(self.bot, game.text_channel, "Votação encerrada! Calculando os resultados... 🔥")
        actions_cog = self.bot.get_cog("ActionsCog")
        if not actions_cog: logger.error(f"[Jogo #{game.text_channel.id}] CRÍTICO: ActionsCog não encontrado."); return
        lynch_result = await actions_cog.process_lynch(game)
        if lynch_result.get("sound_event"): await self.play_sound_effect(game, lynch_result["sound_event"])
        for msg in lynch_result.get("public_messages", []):
            await send_public_message(self.bot, game.text_channel, msg); await asyncio.sleep(1)
        if lynch_result.get("game_over"): return
        if await self.check_game_end(game, "após o linchamento"): return
        if game.current_night >= config.MAX_GAME_NIGHTS:
            await self.check_seventh_day_win(game)
        else:
            await self.start_night(game)

    async def process_death(self, game: GameInstance, target_member: discord.Member, reason: str):
        target_state = game.get_player_state_by_id(target_member.id)
        if not target_state or not target_state.is_alive: return

        logger.info(f"[Jogo #{game.text_channel.id}] Processando morte de {target_member.display_name} por: {reason}.")
        game.death_reasons[target_member.id] = reason
        target_state.kill()
        await self._set_member_mute(target_member, True, "Jogador eliminado")
        if game.first_death_id is None: game.first_death_id = target_member.id

        if isinstance(target_state.role, Fofoqueiro) and game.fofoqueiro_marked_target_id:
            if marked_target_state := game.get_player_state_by_id(game.fofoqueiro_marked_target_id):
                await send_public_message(self.bot, game.text_channel, f"💬 Em seu último suspiro, o Fofoqueiro revela: **{marked_target_state.member.display_name}** era **{marked_target_state.role.name}**!")
        if isinstance(target_state.role, AssassinoJunior) and game.junior_marked_target_id:
            if (marked_target_state := game.get_player_state_by_id(game.junior_marked_target_id)) and marked_target_state.is_alive:
                await send_public_message(self.bot, game.text_channel, f"💥 O espírito vingativo de {target_member.display_name} leva **{marked_target_state.member.display_name}** junto!")
                await self.process_death(game, marked_target_state.member, "killed_by_junior_curse")
                return
        if game.lovers:
            lover1_id, lover2_id = game.lovers
            other_lover_id = lover2_id if target_member.id == lover1_id else (lover1_id if target_member.id == lover2_id else None)
            if other_lover_id and (other_lover_state := game.get_player_state_by_id(other_lover_id)) and other_lover_state.is_alive:
                await send_public_message(self.bot, game.text_channel, f"💔 Ao ver seu amor morrer, **{other_lover_state.member.display_name}** morreu de coração partido!")
                await self.process_death(game, other_lover_state.member, "heartbreak")
                return
        if game.headhunter_info and game.headhunter_info['target_id'] == target_member.id:
            if hunter_state := game.get_player_state_by_id(game.headhunter_info['hunter_id']):
                if hunter_state.is_alive and game.death_reasons.get(target_member.id) != "lynched":
                    hunter_state.role = CidadaoComum()
                    await send_dm_safe(hunter_state.member, "Seu alvo foi eliminado por outros meios. Você se tornou um **Cidadão Comum**.")
                    game.headhunter_info = None
        
        if await self.check_game_end(game, f"após a morte de {target_member.display_name}", victim=target_member):
            return

    async def check_game_end(self, game: GameInstance, context: str, victim: Optional[discord.Member] = None) -> bool:
        if not self.bot.game_manager.get_game(game.text_channel.id): return True
        
        if game.headhunter_info and victim and victim.id == game.headhunter_info['target_id'] and game.death_reasons.get(victim.id) == "lynched":
            if (hunter_state := game.get_player_state_by_id(game.headhunter_info['hunter_id'])) and hunter_state.is_alive:
                await self.end_game(game, "Vitória do Caçador de Cabeças!", [hunter_state.member], "Solo (Caçador de Cabeças)", "O contrato foi cumprido!", sound_event_key="HEADHUNTER_WIN"); return True
        
        alive_players = game.get_alive_players_states()
        if not alive_players:
            await self.end_game(game, "Empate catastrófico!", [], "Ninguém", f"Todos morreram {context}."); return True
        
        villains_alive = [p for p in alive_players if p.role.faction == "Vilões"]
        prefeito_state = next((p for p in game.players.values() if isinstance(p.role, Prefeito)), None)

        if prefeito_state and not prefeito_state.is_alive:
            anjo_pode_reviver = any(isinstance(p.role, Anjo) for p in alive_players) and not game.angel_revive_used
            bruxo_pode_reviver = any(isinstance(p.role, Bruxo) for p in alive_players) and not game.witch_potion_used
            pode_ser_revivido = anjo_pode_reviver or bruxo_pode_reviver
            if not pode_ser_revivido and villains_alive:
                winners = [p.member for p in game.players.values() if p.role.faction == "Vilões"]
                await self.end_game(game, "Vitória dos Vilões!", winners, "Vilões", "A esperança da cidade morreu! O Prefeito não podia mais ser salvo.")
                return True

        if not villains_alive:
            if prefeito_state and prefeito_state.is_alive:
                city_winners = [p.member for p in game.players.values() if p.role.faction == "Cidade"]
                await self.end_game(game, "Vitória da Cidade!", city_winners, "Cidade", "A Cidade eliminou todos os vilões e seu líder permaneceu de pé!")
                return True
            elif prefeito_state and not prefeito_state.is_alive:
                game.pending_resolution = True
                await self._announce_revival_chance(game); await self.start_night(game); return True
            else:
                await self.check_seventh_day_win(game, is_resolution=True); return True

        num_villains, num_non_villains = len(villains_alive), len(alive_players) - len(villains_alive)
        if num_villains >= num_non_villains:
            if victim and (v_state := game.get_player_state_by_id(victim.id)) and v_state.role.faction == "Vilões": return False
            await self.end_game(game, "Vitória dos Vilões!", [p.member for p in villains_alive], "Vilões", "Os Vilões atingiram a paridade!")
            return True
        return False

    async def check_seventh_day_win(self, game: GameInstance, is_resolution: bool = False):
        logger.info(f"[Jogo #{game.text_channel.id}] Verificando vitória do Sétimo Dia.")
        prefeito_state = next((p for p in game.get_alive_players_states() if isinstance(p.role, Prefeito)), None)
        living_villains = [p for p in game.get_alive_players_states() if p.role.faction == "Vilões"]
        if prefeito_state and living_villains and not is_resolution: await self._seventh_day_confrontation(game); return
        
        alive_players = game.get_alive_players_states()
        if game.lovers:
            if (l1 := game.get_player_state_by_id(game.lovers[0])) and (l2 := game.get_player_state_by_id(game.lovers[1])) and l1.is_alive and l2.is_alive:
                winners = [l1.member, l2.member]
                if (cupido := next((p for p in game.players.values() if isinstance(p.role, Cupido)), None)) and cupido.member not in winners: winners.append(cupido.member)
                await self.end_game(game, "Vitória dos Amantes!", list(set(winners)), "Solo (Amantes)", "O amor sobreviveu ao teste do tempo.", sound_event_key="LOVERS_WIN"); return
        if corruptor_state := next((p for p in alive_players if isinstance(p.role, Corruptor)), None):
            await self.end_game(game, "Vitória do Corruptor!", [corruptor_state.member], "Solo (Corruptor)", "Com a cidade em desordem, o Corruptor sobreviveu!", sound_event_key="CORRUPTOR_WIN"); return
        
        winners = [p.member for p in game.players.values() if p.role.faction == "Cidade" and p.is_alive]
        if winners: await self.end_game(game, "Vitória da Cidade!", winners, "Cidade", "A Cidade resistiu bravamente até o fim!"); return
        await self.end_game(game, "Empate por Impasse!", [], "Ninguém", "O tempo acabou e a situação ficou indefinida.")

    async def _seventh_day_confrontation(self, game: GameInstance):
        await send_public_message(self.bot, game.text_channel, "O Sétimo Dia chegou! O destino da cidade será decidido em um **Confronto Final!**")
        await asyncio.sleep(2)
        if await self._sheriff_showdown_loop(game) or not self.bot.game_manager.get_game(game.text_channel.id): return
        await send_public_message(self.bot, game.text_channel, "O Xerife usou suas balas! A iniciativa agora é dos Vilões.")
        await asyncio.sleep(2)
        await self._villain_final_attack(game)

    async def _sheriff_showdown_loop(self, game: GameInstance) -> bool:
        xerife_state = next((p for p in game.get_alive_players_states() if isinstance(p.role, Xerife)), None)
        if not xerife_state or game.sheriff_shots_fired >= 2: return False
        if not game.sheriff_revealed:
            await send_public_message(self.bot, game.text_channel, f"Para o confronto, o Xerife **{xerife_state.member.mention}** se revela!"); game.sheriff_revealed = True; await asyncio.sleep(2)
        while game.sheriff_shots_fired < 2 and self.bot.game_manager.get_game(game.text_channel.id):
            targets = [p.member for p in game.get_alive_players_states() if p.member.id != xerife_state.member.id]
            if not targets: break
            view = ShowdownView(xerife_state.member, targets, timeout=120.0)
            await game.text_channel.send(f"**{xerife_state.member.mention}**, escolha seu alvo para o disparo {game.sheriff_shots_fired + 1}:", view=view)
            await view.wait()
            game.sheriff_shots_fired += 1
            if not view.result: await send_public_message(self.bot, game.text_channel, f"O Xerife {xerife_state.member.mention} não agiu e perdeu uma bala!"); continue
            target_member = game.guild.get_member(view.result)
            await send_public_message(self.bot, game.text_channel, f"{xerife_state.member.mention} atira em **{target_member.mention}**!"); await self.play_sound_effect(game, "SHERIFF_SHOT"); await asyncio.sleep(1)
            target_state = game.get_player_state_by_id(target_member.id)
            if isinstance(target_state.role, Prefeito): await self.end_game(game, "Vitória dos Vilões!", [p.member for p in game.players.values() if p.role.faction == "Vilões"], "Vilões", "Erro fatal! O Xerife eliminou o Prefeito!"); return True
            if isinstance(target_state.role, AssassinoAlfa): await self.end_game(game, "Vitória da Cidade!", [p.member for p in game.players.values() if p.role.faction == "Cidade"], "Cidade", "Tiro certeiro! O Xerife eliminou o Assassino Alfa!"); return True
            await self.process_death(game, target_member, "shot_by_sheriff_showdown"); await asyncio.sleep(2)
        return not self.bot.game_manager.get_game(game.text_channel.id)

    async def _villain_final_attack(self, game: GameInstance):
        vilões_vivos = [p for p in game.get_alive_players_states() if p.role.faction == "Vilões"]
        attacker_state = next((p for p in vilões_vivos if isinstance(p.role, AssassinoAlfa)), None) or next((p for p in vilões_vivos if isinstance(p.role, AssassinoJunior)), None) or next((p for p in vilões_vivos if isinstance(p.role, Cumplice)), None)
        if not attacker_state: return
        await send_public_message(self.bot, game.text_channel, f"A escuridão avança! O **{attacker_state.role.name} {attacker_state.member.mention}** se prepara!"); await asyncio.sleep(2)
        targets = [p.member for p in game.get_alive_players_states() if p.role.faction == "Cidade"]
        if not targets: await self.end_game(game, "Vitória dos Vilões!", [p.member for p in vilões_vivos], "Vilões", "Não restaram alvos para o ataque final!"); return
        view = ShowdownView(attacker_state.member, targets, timeout=120.0)
        await game.text_channel.send(f"**{attacker_state.member.mention}**, escolha seu alvo para o ataque final:", view=view)
        await view.wait()
        if not view.result: await self.end_game(game, "Vitória da Cidade!", [p.member for p in game.players.values() if p.role.faction == "Cidade"], "Cidade", "Os Vilões hesitaram e a Cidade venceu!"); return
        target_member = game.guild.get_member(view.result)
        await send_public_message(self.bot, game.text_channel, f"O {attacker_state.role.name} ataca **{target_member.mention}**!"); await asyncio.sleep(2)
        if isinstance(game.get_player_state_by_id(target_member.id).role, Prefeito):
            await self.end_game(game, "Vitória dos Vilões!", [p.member for p in vilões_vivos], "Vilões", f"O {attacker_state.role.name} eliminou o Prefeito!")
        else:
            await self.end_game(game, "Vitória da Cidade!", [p.member for p in game.players.values() if p.role.faction == "Cidade"], "Cidade", "O Prefeito sobreviveu ao ataque final!")

    async def _check_and_award_bruxo_win(self, game: GameInstance, winners: List[discord.Member], winning_faction: str):
        if bruxo_state := next((p for p in game.players.values() if isinstance(p.role, Bruxo)), None):
            bruxo_wins = False
            for action in game.successful_major_actions:
                if action['actor'] == bruxo_state.member.id and (target_state := game.get_player_state_by_id(action['target'])):
                    if action['action'] == 'kill' and ((isinstance(target_state.role, Prefeito) and winning_faction == "Vilões") or (isinstance(target_state.role, AssassinoAlfa) and winning_faction == "Cidade")): bruxo_wins = True; break
                    elif action['action'] == 'revive' and target_state.role and target_state.role.faction == winning_faction: bruxo_wins = True; break
            if bruxo_wins and bruxo_state.member not in winners: winners.append(bruxo_state.member)
        return winners

    async def _check_and_award_lovers_win(self, game: GameInstance, winners: List[discord.Member]):
        if game.lovers:
            lover1_id, lover2_id = game.lovers
            winner_ids = {w.id for w in winners}
            if lover1_id in winner_ids or lover2_id in winner_ids:
                if (l1_state := game.get_player_state_by_id(lover1_id)) and l1_state.is_alive and l1_state.member not in winners: winners.append(l1_state.member)
                if (l2_state := game.get_player_state_by_id(lover2_id)) and l2_state.is_alive and l2_state.member not in winners: winners.append(l2_state.member)
                if (cupido := next((p for p in game.players.values() if isinstance(p.role, Cupido)), None)) and cupido.member not in winners: winners.append(cupido.member)
        return list(set(winners))

    async def _check_and_award_fofoqueiro_win(self, game: GameInstance, winners: List[discord.Member], winning_faction: str):
        if winning_faction in ["Cidade", "Vilões"]:
            if (fofoqueiro_state := next((p for p in game.get_alive_players_states() if isinstance(p.role, Fofoqueiro)), None)) and fofoqueiro_state.member not in winners:
                winners.append(fofoqueiro_state.member)
        return winners

    async def end_game(self, game: GameInstance, title: str, winners: List[discord.Member], faction: str, reason: str, error: bool = False, sound_event_key: Optional[str] = None):
        if not self.bot.game_manager.get_game(game.text_channel.id) and not error: return
        final_winners = await self._check_and_award_fofoqueiro_win(game, await self._check_and_award_lovers_win(game, await self._check_and_award_bruxo_win(game, list(winners), faction)), faction)
        final_faction_name = f"Solo ({sound_event_key.replace('_WIN', '').title()})" if sound_event_key and "WIN" in sound_event_key else faction
        
        embed = discord.Embed(title=f"🏁 FIM DE JOGO: {title} 🏁", description=f"**Motivo:** {reason}", color=discord.Color.gold())
        embed.add_field(name=f"🏆 Vencedores ({final_faction_name})", value=("\n".join([w.mention for w in final_winners]) if final_winners else "Ninguém"), inline=False)
        roles_text = "\n".join([f"- {p.member.mention}: **{p.role.name}** ({p.role.faction})" for p in game.players.values() if p.role]) or "Não foi possível revelar os papéis."
        embed.add_field(name="🕵️ Papéis Revelados 🕵️", value=roles_text, inline=False)
        
        image_key = sound_event_key if sound_event_key and config.EVENT_IMAGES.get(sound_event_key) else ("CITY_WIN" if "Cidade" in faction else ("VILLAINS_WIN" if "Vilões" in faction else None))
        image_path = os.path.join(config.IMAGES_PATH, config.EVENT_IMAGES.get(image_key, "")) if image_key else None
        
        await send_public_message(self.bot, game.text_channel, embed=embed, file_path=image_path if image_path and os.path.exists(image_path) else None)
        
        # Envia a mensagem de créditos a partir do config.py
        await asyncio.sleep(2)
        await send_public_message(self.bot, game.text_channel, message=config.MSG_CREDITS)

        game.current_phase = "finished"
        game.winning_faction = faction
        if game.current_timer_task and not game.current_timer_task.done(): game.current_timer_task.cancel()
        
        await self._update_voice_permissions(game, mute=False, force_unmute_all=True)
        if (voice_client := discord.utils.get(self.bot.voice_clients, guild=game.guild)) and voice_client.is_connected():
            if image_key: await self.play_sound_effect(game, image_key, wait_for_finish=True)
            await voice_client.disconnect(force=True)

        if not error:
            if ranking_cog := self.bot.get_cog("RankingCog"):
                await ranking_cog.update_stats_after_game(game, final_winners)
        
        self.bot.game_manager.end_game(game.text_channel.id)
        await self.bot.change_presence(activity=discord.Game(name="Cidade Dorme | /preparar"))

def setup(bot: commands.Bot):
    bot.add_cog(GameFlowCog(bot))