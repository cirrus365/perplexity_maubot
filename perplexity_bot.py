# Perplexity Sonar Maubot Plugin

import asyncio
import json
import re
from typing import Type

from maubot import Plugin, MessageEvent
from maubot.handlers import command, event
from mautrix.types import Format, TextMessageEventContent, EventType, MessageType, RelationType
from mautrix.util import markdown
from mautrix.util.config import BaseProxyConfig, ConfigUpdateHelper

class Config(BaseProxyConfig):
    def do_update(self, helper: ConfigUpdateHelper) -> None:
        helper.copy("openrouter_api_key")
        helper.copy("model")
        helper.copy("max_tokens")
        helper.copy("temperature")
        helper.copy("allowed_users")
        helper.copy("name")
        helper.copy("max_context_messages")
        helper.copy("reply_in_thread")
        helper.copy("system_prompt")

class PerplexityBot(Plugin):

    async def start(self) -> None:
        await super().start()
        self.config.load_and_update()
        self.name = self.config.get("name", "fxivity")
        self.api_endpoint = "https://openrouter.ai/api/v1/chat/completions"
        self.log.info(f"Perplexity plugin started with bot name: {self.name}")

    def user_allowed(self, mxid: str) -> bool:
        allowed_users = self.config.get("allowed_users", [])
        if not allowed_users:
            return True
        for pattern in allowed_users:
            if re.match(pattern, mxid):
                return True
        return False

    async def should_respond(self, evt: MessageEvent) -> bool:
        # Ignore self, commands, and non-text messages
        if (evt.sender == self.client.mxid or
                evt.content.body.startswith('!') or
                evt.content.get("msgtype") != MessageType.TEXT):
            return False

        # Check for bot name mention or sonar command
        body = evt.content.body
        bot_name = self.name
        if (re.search(r"(^|\s)(@)?" + re.escape(bot_name) + r"([ :,.!?]|$)", body, re.IGNORECASE) or
            body.startswith('!sonar')):
            return self.user_allowed(evt.sender)

        # Reply to DMs
        try:
            members = await self.client.get_joined_members(evt.room_id)
            if len(members) == 2:
                return self.user_allowed(evt.sender)
        except Exception:
            pass

        # Reply to messages replying to bot by checking if parent message has custom metadata
        if (hasattr(evt.content, 'relates_to') and 
            hasattr(evt.content.relates_to, 'in_reply_to') and 
            evt.content.relates_to.in_reply_to):
            try:
                parent_event = await self.client.get_event(
                    room_id=evt.room_id, 
                    event_id=evt.content.get_reply_to()
                )
                if (parent_event and 
                    parent_event.sender == self.client.mxid and 
                    hasattr(parent_event, 'content') and
                    "org.example.perplexity" in parent_event.content):
                    return self.user_allowed(evt.sender)
            except Exception as e:
                self.log.warning(f"Failed to get parent event: {e}")

        return False

    @event.on(EventType.ROOM_MESSAGE)
    async def on_message(self, evt: MessageEvent) -> None:
        if not await self.should_respond(evt):
            return

        try:
            await evt.mark_read()
            await self.client.set_typing(evt.room_id, timeout=30000)

            # Extract query
            query = evt.content.body
            if query.startswith('!sonar'):
                query = query[6:].strip()
            else:
                # Remove bot name from query
                query = re.sub(r'@?' + re.escape(self.name) + r'[\s:,.!?]*', '', query, flags=re.IGNORECASE).strip()

            # Call API
            response = await self._call_openrouter(query)

            # Send response
            content = TextMessageEventContent(
                msgtype=MessageType.NOTICE,
                body=response,
                format=Format.HTML,
                formatted_body=markdown.render(response)
            )
            content["org.example.perplexity"] = True
            await evt.respond(content)

        except Exception as e:
            self.log.exception(f"Error handling message: {e}")
            await evt.respond(f"Something went wrong: {e}")
        finally:
            await self.client.set_typing(evt.room_id, timeout=0)

    async def _call_openrouter(self, query: str) -> str:
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.config['openrouter_api_key']}",
            "HTTP-Referer": "https://github.com/example/perplexity-maubot",
            "X-Title": "Perplexity Maubot Plugin"
        }
        
        data = {
            "model": self.config.get("model", "perplexity/sonar-pro"),
            "messages": [{"role": "user", "content": query}],
        }

        max_tokens = self.config.get("max_tokens", None)
        if max_tokens:
            data["max_tokens"] = max_tokens
            
        temperature = self.config.get("temperature", None)
        if temperature is not None:
            data["temperature"] = temperature

        async with self.http.post(self.api_endpoint, headers=headers, data=json.dumps(data)) as response:
            if response.status != 200:
                error_text = await response.text()
                self.log.error(f"OpenRouter API error: {response.status} - {error_text}")
                return f"Error: API returned status {response.status}"
            
            response_json = await response.json()
            return response_json["choices"][0]["message"]["content"]

    @command.new(name='sonar', help='Search with Perplexity Sonar AI')
    @command.argument('query', pass_raw=True, required=True)
    async def sonar(self, evt: MessageEvent, query: str) -> None:
        # Process the command using the same logic as message handler
        await self.on_message(evt)

    @classmethod
    def get_config_class(cls) -> Type[BaseProxyConfig]:
        return Config
