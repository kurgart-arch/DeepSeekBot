#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DeepSeek AI client for direct API integration
"""

import aiohttp
import asyncio
import json
import logging
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)

class OpenRouterClient:
    """
    Клиент для работы с DeepSeek API напрямую.
    Название класса оставлено для совместимости с existing code.
    """
    def __init__(self, api_key: str):
        self.api_key = api_key
        # Правильный базовый URL для DeepSeek API (без /v1)
        self.base_url = "https://api.deepseek.com"
        self.model = "deepseek-reasoner"  # или "deepseek-reasoner" для R1
        self.session = None
        
    async def _get_session(self):
        """Get or create aiohttp session"""
        if self.session is None or self.session.closed:
            timeout = aiohttp.ClientTimeout(total=30)
            self.session = aiohttp.ClientSession(timeout=timeout)
        return self.session
    
    async def generate_response(self, messages: List[Dict], max_tokens: int = 600) -> Optional[str]:
        """Generate response using DeepSeek API"""
        try:
            session = await self._get_session()
            
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            }
            
            payload = {
                "model": self.model,
                "messages": messages,
                "max_tokens": min(max_tokens, 600),
                "temperature": 0.7,
                "top_p": 0.9,
                "frequency_penalty": 0.1,
                "presence_penalty": 0.1,
                "stream": False
            }
            
            logger.info(f"Sending request to DeepSeek with {len(messages)} messages")
            
            # Эндпоинт для chat completions
            async with session.post(f"{self.base_url}/chat/completions", 
                                  headers=headers, 
                                  json=payload) as response:
                
                if response.status == 200:
                    data = await response.json()
                    
                    if "choices" in data and len(data["choices"]) > 0:
                        content = data["choices"][0]["message"]["content"]
                        logger.info(f"Generated response: {len(content)} characters")
                        return content.strip()
                    else:
                        logger.error(f"No choices in response: {data}")
                        return None
                        
                else:
                    error_text = await response.text()
                    logger.error(f"DeepSeek API error {response.status}: {error_text}")
                    return None
                    
        except asyncio.TimeoutError:
            logger.error("Timeout while calling DeepSeek API")
            return None
        except aiohttp.ClientError as e:
            logger.error(f"HTTP client error: {e}")
            return None
        except json.JSONDecodeError as e:
            logger.error(f"JSON decode error: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error calling DeepSeek API: {e}")
            return None
    
    async def close(self):
        """Close the aiohttp session"""
        if self.session and not self.session.closed:
            await self.session.close()
    
    def __del__(self):
        """Cleanup on destruction"""
        if self.session and not self.session.closed:
            try:
                asyncio.get_event_loop().run_until_complete(self.close())
            except:
                pass
