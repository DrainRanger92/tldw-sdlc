from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib

DEFAULT_PROTECTED = (
    "orchestrator/", ".opencode/", "dsh/", "aidevteam", "docker/", "aidevteam.toml",
)

SUPPORTED_BACKENDS = ("opencode", "dsh")


@dataclass
class Config:
    watch_interval_sec: int = 300
    max_agent_calls: int = 40
    max_review_iterations: int = 2
    stage_timeout_sec: int = 1800
    app_port: int = 3000
    protected_paths: tuple[str, ...] = DEFAULT_PROTECTED
    agent_author_name: str = "aidevteam-agent"
    agent_author_email: str = "agent@aidevteam.local"
    push: str = "auto"  # auto | never

    # Бэкенд агентов: opencode (legacy) или dsh.
    agents_backend: str = "opencode"
    # Роль -> "provider/model"; отсутствующая роль использует дефолт профиля.
    agent_models: dict[str, str] = field(default_factory=dict)

    dsh_bin: str = "dsh"
    dsh_version: str = ""
    dsh_profile: str = "headless"
    dsh_permission_mode: str = "danger-full-access"
    dsh_reviewer_permission_mode: str = "read-only"
    dsh_approval_policy: str = "never"
    dsh_roles_dir: str = "dsh/roles"
    dsh_env_file: str = ".env"
    dsh_home: str = ""  # пусто — унаследовать DSH_HOME/дефолт DSH

    @property
    def max_opencode_calls(self) -> int:
        """Совместимость с прежним именем поля."""
        return self.max_agent_calls

    def validate(self) -> None:
        if self.agents_backend not in SUPPORTED_BACKENDS:
            raise ValueError(
                "agents.backend должен быть opencode или dsh, "
                f"получено: {self.agents_backend!r}"
            )
        for role, spec in self.agent_models.items():
            if spec.count("/") != 1 or not all(spec.split("/", 1)):
                raise ValueError(
                    f"agents.models.{role}: ожидается provider/model, получено {spec!r}"
                )

    @classmethod
    def load(cls, root: Path) -> "Config":
        cfg = cls()
        f = root / "aidevteam.toml"
        if not f.exists():
            cfg._apply_backend_env()
            return cfg
        data = tomllib.loads(f.read_text(encoding="utf-8"))
        cycle = data.get("cycle", {})
        watch = data.get("watch", {})
        app = data.get("app", {})
        for key in ("max_review_iterations", "stage_timeout_sec"):
            if key in cycle:
                setattr(cfg, key, int(cycle[key]))
        if "max_agent_calls" in cycle:
            cfg.max_agent_calls = int(cycle["max_agent_calls"])
        elif "max_opencode_calls" in cycle:  # старое имя ключа
            cfg.max_agent_calls = int(cycle["max_opencode_calls"])
        if "interval_sec" in watch:
            cfg.watch_interval_sec = int(watch["interval_sec"])
        if "port" in app:
            cfg.app_port = int(app["port"])
        git = data.get("git", {})
        if git.get("push") in ("auto", "never"):
            cfg.push = git["push"]

        agents = data.get("agents", {})
        if "backend" in agents:
            cfg.agents_backend = str(agents["backend"]).strip().lower()
        models = agents.get("models", {})
        if isinstance(models, dict):
            cfg.agent_models = {str(k): str(v) for k, v in models.items()}
        dsh = agents.get("dsh", {})
        if isinstance(dsh, dict):
            for key, attr in (
                ("bin", "dsh_bin"), ("version", "dsh_version"),
                ("profile", "dsh_profile"),
                ("permission_mode", "dsh_permission_mode"),
                ("reviewer_permission_mode", "dsh_reviewer_permission_mode"),
                ("approval_policy", "dsh_approval_policy"),
                ("roles_dir", "dsh_roles_dir"), ("env_file", "dsh_env_file"),
                ("home", "dsh_home"),
            ):
                if key in dsh:
                    setattr(cfg, attr, str(dsh[key]))
        cfg._apply_backend_env()
        return cfg

    def _apply_backend_env(self) -> None:
        """Env-переопределение нужно тестам и CI, не требуя править toml."""
        env = os.environ.get("AIDEVTEAM_AGENTS_BACKEND")
        if env:
            self.agents_backend = env.strip().lower()
