# -*- coding: utf-8 -*-
"""
AstrBot 每日单词卡片插件
每日定时生成单词卡片并推送到群聊，支持用户独立进度。
"""
import asyncio
import datetime
import json
import os
import traceback
from pathlib import Path
from typing import Optional, Dict, List

from astrbot.api import logger, AstrBotConfig
from astrbot.api.event import filter, AstrMessageEvent, MessageChain
from astrbot.api.star import Context, Star, register

from . import actions
from .card_generator import generate_card_image
from .progress_manager import ProgressManager
from .utils import get_beijing_time

# This file is intentionally left blank.



@register("vocabcard", "Assistant", "每日英语单词卡片（Pillow版）", "2.0.0")
class VocabCardPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.plugin_dir = Path(__file__).parent
        self.data_dir = self.plugin_dir / "data"

        # 加载词汇数据
        self.words: List[Dict] = self._load_words()
        
        # 初始化进度管理器
        self.progress_manager = ProgressManager(self.data_dir, self.words)

        # 定时任务相关
        self._scheduler_task: Optional[asyncio.Task] = None
        self._cached_image_path: Optional[str] = None
        self._current_word: Optional[Dict] = None
        self._today_generated: bool = False
        self._last_check_date: str = ""

    def _load_words(self) -> List[Dict]:
        """加载词汇数据"""
        words_file = self.data_dir / "words.json"
        if words_file.exists():
            try:
                with open(words_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                logger.error(f"加载词汇数据失败: {e}")
        return []

    async def initialize(self):
        """异步初始化"""
        logger.info(f"单词卡片插件 v2 初始化完成，已加载 {len(self.words)} 个单词")

    @filter.on_astrbot_loaded()
    async def on_loaded(self):
        """AstrBot 启动后启动定时任务"""
        self._scheduler_task = asyncio.create_task(self._schedule_loop())
        logger.info("单词卡片定时任务已启动")

    async def _schedule_loop(self):
        """定时任务主循环 - 智能睡眠，精准触发"""
        while True:
            try:
                now = get_beijing_time()
                today_str = now.strftime("%Y-%m-%d")

                # 解析配置的时间
                gen_time = self._parse_time(self.config.get("push_time_generate", "07:30"))
                push_time = self._parse_time(self.config.get("push_time_send", "08:00"))

                # 每天0点重置标记
                if self._last_check_date != today_str:
                    self._today_generated = False
                    self._last_check_date = today_str

                # 计算下一个目标时间
                next_target = self._calculate_next_target_time(now, gen_time, push_time)

                if next_target:
                    sleep_seconds = (next_target - now).total_seconds()

                    # 如果距离目标时间超过 60 秒，先睡到提前 30 秒
                    if sleep_seconds > 60:
                        sleep_until = sleep_seconds - 30
                        logger.debug(f"距离下次任务还有 {sleep_seconds:.0f} 秒，先睡眠 {sleep_until:.0f} 秒")
                        await asyncio.sleep(sleep_until)
                        continue

                    # 距离目标时间很近了，精确等待
                    if sleep_seconds > 0:
                        logger.debug(f"即将执行任务，精确等待 {sleep_seconds:.1f} 秒")
                        await asyncio.sleep(sleep_seconds)

                # 重新获取当前时间（睡眠后）
                now = get_beijing_time()

                # 执行生成任务
                if now.hour == gen_time[0] and now.minute == gen_time[1]:
                    if not self._today_generated:
                        logger.info("开始生成每日单词卡片...")
                        await self._generate_daily_card()
                        self._today_generated = True

                # 执行推送任务
                if now.hour == push_time[0] and now.minute == push_time[1]:
                    if self._cached_image_path and os.path.exists(self._cached_image_path):
                        logger.info("开始推送每日单词卡片...")
                        await self._push_daily_card()

                # 执行完任务后等待 10 秒，避免重复触发
                await asyncio.sleep(10)

            except Exception as e:
                logger.error(f"定时任务出错: {e}")
                await asyncio.sleep(60)  # 出错后等待 60 秒重试

    def _parse_time(self, time_str: str) -> tuple:
        """解析时间字符串 HH:MM"""
        try:
            parts = time_str.split(':')
            return (int(parts[0]), int(parts[1]))
        except:
            return (8, 0)  # 默认 8:00

    def _calculate_next_target_time(self, now: datetime.datetime, gen_time: tuple, push_time: tuple) -> Optional[datetime.datetime]:
        """计算下一个目标时间点（生成时间或推送时间中最近的一个）"""
        today = now.date()

        # 构建今天的生成时间和推送时间
        gen_datetime = datetime.datetime.combine(today, datetime.time(gen_time[0], gen_time[1]))
        push_datetime = datetime.datetime.combine(today, datetime.time(push_time[0], push_time[1]))

        # 找出所有未来的目标时间
        targets = []

        # 如果还没生成过，且生成时间未到
        if not self._today_generated and gen_datetime > now:
            targets.append(gen_datetime)

        # 如果推送时间未到
        if push_datetime > now:
            targets.append(push_datetime)

        # 如果今天的任务都完成了，计算明天的第一个任务（生成时间）
        if not targets:
            tomorrow = today + datetime.timedelta(days=1)
            next_gen = datetime.datetime.combine(tomorrow, datetime.time(gen_time[0], gen_time[1]))
            targets.append(next_gen)

        # 返回最近的目标时间
        return min(targets) if targets else None

    async def _generate_daily_card(self):
        """生成每日单词卡片（用于全局推送）"""
        mode = self.config.get("learning_mode", "random")
        word = self.progress_manager.select_word(user_id=None, mode=mode)
        if not word:
            logger.warning("没有可用的单词用于全局推送")
            return

        try:
            image_path = generate_card_image(word, self.plugin_dir)
            self._cached_image_path = image_path
            self._current_word = word
            self.progress_manager.mark_word_sent(word["word"], user_id=None)
            logger.info(f"已生成每日单词卡片: {word['word']}")
        except Exception as e:
            logger.error(f"生成每日卡片失败: {e}\n{traceback.format_exc()}")

    async def _push_daily_card(self):
        """推送卡片到已注册的群聊"""
        if not self._cached_image_path or not os.path.exists(self._cached_image_path):
            logger.warning("没有已生成的卡片可推送")
            return

        target_groups = self.config.get("target_groups", [])
        if not target_groups:
            logger.warning("没有已注册的推送目标")
            return

        success_count = 0
        word_text = self._current_word.get("word", "单词") if self._current_word else "单词"

        for umo in target_groups:
            try:
                # 构建消息链
                chain = MessageChain()
                chain.message(f"📚 每日单词: {word_text}")
                chain.file_image(self._cached_image_path)

                await self.context.send_message(umo, chain)
                success_count += 1
                logger.info(f"已推送到: {umo}")
            except Exception as e:
                logger.error(f"推送到 {umo} 失败: {e}")

        logger.info(f"每日单词推送完成: {success_count}/{len(target_groups)}")

        # 清理缓存的图片
        try:
            if os.path.exists(self._cached_image_path):
                os.remove(self._cached_image_path)
        except:
            pass
        self._cached_image_path = None

    @filter.command("vocab")
    async def cmd_vocab(self, event: AstrMessageEvent):
        """手动获取一个单词卡片（记录个人进度）"""
        async for result in actions.handle_vocab(self, event):
            yield result

    @filter.command("vocab_recap")
    async def cmd_vocab_recap(self, event: AstrMessageEvent, count: str = "1"):
        """复习已学习的单词"""
        async for result in actions.handle_vocab_recap(self, event, count):
            yield result

    @filter.command("vocab_status")
    async def cmd_status(self, event: AstrMessageEvent):
        """查看个人和全局的学习进度"""
        async for result in actions.handle_status(self, event):
            yield result

    @filter.command("vocab_register")
    async def cmd_register(self, event: AstrMessageEvent):
        """在当前会话注册接收每日单词推送"""
        async for result in actions.handle_register(self, event):
            yield result

    @filter.command("vocab_unregister")
    async def cmd_unregister(self, event: AstrMessageEvent):
        """取消当前会话的每日单词推送"""
        async for result in actions.handle_unregister(self, event):
            yield result

    @filter.command("vocab_test")
    async def cmd_test_push(self, event: AstrMessageEvent, delay_seconds: str = "0"):
        """测试推送功能"""
        async for result in actions.handle_test_push(self, event, delay_seconds):
            yield result

    @filter.command("vocab_preview")
    async def cmd_preview(self, event: AstrMessageEvent, word_input: str = ""):
        """预览单词卡片效果（调试用）"""
        async for result in actions.handle_preview(self, event, word_input):
            yield result

    @filter.command("vocab_now")
    async def cmd_push_now(self, event: AstrMessageEvent):
        """立即执行一次完整的生成+推送流程（模拟定时任务）"""
        async for result in actions.handle_push_now(self, event):
            yield result

    @filter.command("vocab_help")
    async def cmd_help(self, event: AstrMessageEvent):
        """显示帮助信息"""
        async for result in actions.handle_help(self, event):
            yield result

    async def terminate(self):
        """插件卸载时取消定时任务"""
        if self._scheduler_task:
            self._scheduler_task.cancel()
        logger.info("单词卡片插件已卸载")
