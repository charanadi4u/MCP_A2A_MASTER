# utilities/mcp/mcp_connect.py

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)


def _repo_root() -> Path:
    # utilities/mcp/mcp_connect.py -> utilities/mcp -> utilities -> repo root
    return Path(__file__).resolve().parents[2]


def _load_servers_from_file(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        raise FileNotFoundError(f"MCP servers config not found: {path}")

    if path.suffix.lower() in [".yaml", ".yml"]:
        try:
            import yaml  # type: ignore
        except Exception as e:
            raise RuntimeError(
                f"YAML config requested but PyYAML is not installed. "
                f"Install pyyaml or use JSON. ({e})"
            )
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    else:
        data = json.loads(path.read_text(encoding="utf-8"))

    # support a few shapes:
    # 1) {"servers": [...]}
    # 2) {"mcpServers": [...]}
    # 3) [...]
    if isinstance(data, dict):
        servers = data.get("servers") or data.get("mcpServers") or data.get("mcp_servers")
        if servers is None:
            raise ValueError(f"No 'servers' key found in {path}")
        if not isinstance(servers, list):
            raise TypeError(f"'servers' must be a list in {path}")
        return servers

    if isinstance(data, list):
        return data

    raise TypeError(f"Unsupported config format in {path}: expected dict or list")


def _default_servers_config_path() -> Optional[Path]:
    # 1) explicit env var wins
    env_path = os.getenv("MCP_SERVERS_PATH") or os.getenv("MCP_CONFIG_PATH")
    if env_path:
        return Path(env_path)

    # 2) common file locations in repos
    root = _repo_root()
    candidates = [
        root / "mcp_servers.json",
        root / "mcp_servers.yaml",
        root / "mcp_servers.yml",
        root / "configs" / "mcp_servers.json",
        root / "configs" / "mcp_servers.yaml",
        root / "configs" / "mcp_servers.yml",
        root / "config" / "mcp_servers.json",
        root / "config" / "mcp_servers.yaml",
        root / "config" / "mcp_servers.yml",
    ]
    for p in candidates:
        if p.exists():
            return p

    return None


class MCPConnector:
    def __init__(
        self,
        servers: Optional[List[Dict[str, Any]]] = None,
        timeout_s: float = 10.0,
    ):
        self.timeout_s = timeout_s
        self._tools: List[Any] = []
        self._loaded = False

        if servers is not None:
            self.servers = servers
        else:
            cfg_path = _default_servers_config_path()
            if cfg_path is None:
                logger.warning(
                    "No MCP servers config found. Set MCP_SERVERS_PATH or add mcp_servers.json under repo root/configs/config."
                )
                self.servers = []
            else:
                try:
                    self.servers = _load_servers_from_file(cfg_path)
                    logger.info("Loaded MCP servers config from %s (%d servers)", cfg_path, len(self.servers))
                except Exception:
                    logger.exception("Failed to load MCP servers config from %s", cfg_path)
                    self.servers = []

    async def _load_all_tools(self) -> None:
        if self._loaded:
            return

        loaded_tools: List[Any] = []
        failures: List[str] = []

        for server_cfg in self.servers:
            name = server_cfg.get("name", "<unnamed>")
            try:
                toolset = self._build_toolset(server_cfg)

                tools = await asyncio.wait_for(toolset.get_tools(), timeout=self.timeout_s)
                loaded_tools.extend(tools)
                logger.info("Loaded %d tools from MCP server '%s'", len(tools), name)

            except FileNotFoundError as e:
                failures.append(f"{name}: command not found ({e})")
                logger.warning("Skipping MCP server '%s' (%s)", name, e)

            except (httpx.ConnectError, httpx.TimeoutException) as e:
                failures.append(f"{name}: cannot connect ({e})")
                logger.warning("Skipping MCP server '%s' (connect/timeout: %s)", name, e)

            except asyncio.TimeoutError:
                failures.append(f"{name}: get_tools() timed out after {self.timeout_s}s")
                logger.warning("Skipping MCP server '%s' (timeout after %ss)", name, self.timeout_s)

            except Exception as e:
                failures.append(f"{name}: unexpected error ({type(e).__name__}: {e})")
                logger.exception("Skipping MCP server '%s' (unexpected error)", name)

        self._tools = loaded_tools
        self._loaded = True

        if failures:
            logger.warning("MCP servers skipped (%d):\n- %s", len(failures), "\n- ".join(failures))

    async def get_tools(self) -> List[Any]:
        await self._load_all_tools()
        return list(self._tools)

    def _build_toolset(self, server_cfg: Dict[str, Any]):
        """
        KEEP your existing implementation here.
        This should return the ADK MCPToolset instance for server_cfg.
        """
        raise NotImplementedError
