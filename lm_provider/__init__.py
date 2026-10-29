"""LM API Manager: a local OpenAI-compatible gateway that adds the user's own API keys.

Pal Engine talks to it over 127.0.0.1; the keys never leave this process except in the
upstream request to the provider the user chose.
"""

__version__ = "0.2.0"
