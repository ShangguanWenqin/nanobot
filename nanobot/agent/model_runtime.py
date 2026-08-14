"""Public resolution boundary for default and overridden LLM runtimes.
Agent 的“模型运行时选择器 + Runtime 生命周期管理器”。

它主要解决 4 个问题：

1. 当前默认模型是谁？
2. 切换 preset 后，未来 Turn 用谁？
3. 配置发生变化后，默认 Runtime 是否需要刷新？
4. 某一次 SDK/API 调用想临时指定模型时，怎么不影响默认模型？
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import replace
from types import MappingProxyType
from typing import cast

from nanobot.agent import model_presets as preset_helpers
from nanobot.config.schema import Config, ModelPresetConfig
from nanobot.providers.factory import ProviderSnapshot, build_provider_snapshot
from nanobot.utils.llm_runtime import LLMRuntime, runtime_from_provider_snapshot


class ModelRuntimeResolver:
    """Own model selection and resolve it to immutable execution values.

    The resolver is deliberately independent of ``AgentLoop``.  Command, SDK,
    and tool admission layers can depend on this public service without reading
    or mutating private loop state.
    """

    def __init__(
        self,
        initial_runtime: LLMRuntime,
        *,
        model_presets: Mapping[str, ModelPresetConfig] | None = None,
        preset_catalog_loader: preset_helpers.PresetCatalogLoader | None = None,
        configured_default_preset: str | None = None,
        provider_snapshot_loader: Callable[[], ProviderSnapshot] | None = None,
        preset_snapshot_loader: preset_helpers.PresetSnapshotLoader | None = None,
    ) -> None:
        self._runtime = initial_runtime # 初始runtime
        self._model_presets = dict(model_presets or {}) # 预设model 配置
        self._preset_catalog_loader = preset_catalog_loader
        self._preset_catalog_refresh_required = False
        self._provider_snapshot_loader = provider_snapshot_loader 
        self._preset_snapshot_loader = preset_snapshot_loader
        self._refresh_required = False
        self._resolved_presets: dict[str, LLMRuntime] = {}
        self._tracks_provider_generation = initial_runtime.model_preset is None
        self._default_selection_signature = preset_helpers.default_selection_signature(
            initial_runtime.snapshot_signature,
            configured_default_preset,
        )

    @property
    def runtime(self) -> LLMRuntime:
        """Return the current immutable default without refreshing configuration."""
        return self._runtime

    @property
    def model_presets(self) -> Mapping[str, ModelPresetConfig]:
        self._refresh_preset_catalog()
        return MappingProxyType({
            name: preset.model_copy(deep=True)
            for name, preset in self._model_presets.items()
        })

    @property
    def model_preset(self) -> str | None:
        return self._runtime.model_preset

    @property
    def provider_signature(self) -> tuple[object, ...] | None:
        return self._runtime.snapshot_signature

    # 返回当前的runtime，可选择是否刷新
    def current(self, *, refresh: bool = False) -> LLMRuntime:
        """Return the selected runtime, optionally refreshing the default source."""
        if refresh:
            self.refresh()
            self._refresh_provider_generation()
        return self._runtime

    def admit(self) -> LLMRuntime:
        """Resolve the immutable runtime for the next turn admission."""
        if self._refresh_required:
            self.refresh()
        self._refresh_provider_generation()
        return self._runtime

    def invalidate(self) -> None:
        """Refresh configured runtime state on the next admission."""
        self._refresh_required = True
        self._preset_catalog_refresh_required = True
        self._resolved_presets.clear()

    def _refresh_preset_catalog(self) -> None:
        if not self._preset_catalog_refresh_required:
            return
        if self._preset_catalog_loader is not None:
            self._model_presets = dict(self._preset_catalog_loader())
        self._preset_catalog_refresh_required = False

    def resolve_snapshot(
        self,
        snapshot: ProviderSnapshot,
    ) -> LLMRuntime:
        """Resolve a factory snapshot without changing the selected default."""
        return runtime_from_provider_snapshot(snapshot)

    # 应用一个快照
    def adopt_snapshot(
        self,
        snapshot: ProviderSnapshot,
    ) -> LLMRuntime:
        """Select a snapshot as the default for future turns."""
        runtime = self.resolve_snapshot(snapshot)
        self._runtime = runtime
        self._tracks_provider_generation = runtime.model_preset is None
        self._default_selection_signature = preset_helpers.default_selection_signature(
            runtime.snapshot_signature,
            runtime.model_preset,
        )
        return runtime

    # 解析一个preset name， 返回runtime
    def resolve_preset(self, name: str | None) -> LLMRuntime:
        """Resolve a named preset without changing the selected default."""
        self._refresh_preset_catalog()
        normalized = preset_helpers.normalize_preset_name(name, self._model_presets)
        cached = self._resolved_presets.get(normalized)
        if cached is not None:
            return cached
        snapshot = preset_helpers.build_runtime_preset_snapshot(
            name=normalized,
            presets=self._model_presets,
            provider=self._runtime.provider,
            loader=self._preset_snapshot_loader,
        )
        runtime = self.resolve_snapshot(snapshot)
        self._resolved_presets[normalized] = runtime
        return runtime

    # 选择一个preset name（非临时）
    def select_preset(self, name: str | None) -> LLMRuntime:
        """Select a named preset as the default for future turns."""
        runtime = self.resolve_preset(name)
        self._runtime = runtime
        self._tracks_provider_generation = False
        return runtime

    # runtime换model，注意一旦你直接选择了 model，就不再认为当前 Runtime 是某个 preset 的完整配置。所以runtime里的model_preset=None
    def select_model(self, model: str) -> LLMRuntime:
        """Change the default model without reconstructing downstream consumers."""
        if not isinstance(cast(object, model), str) or not model.strip():
            raise ValueError("model must be a non-empty string")
        self._runtime = replace(
            self._runtime,
            model=model.strip(),
            model_preset=None,
        )
        return self._runtime

    # 改变上下文token限制
    def select_context_window(self, context_window_tokens: int) -> LLMRuntime:
        """Change the default context limit for future admissions."""
        raw_context_window = cast(object, context_window_tokens)
        if not isinstance(raw_context_window, int) or isinstance(
            raw_context_window,
            bool,
        ):
            raise TypeError("context_window_tokens must be an integer")
        self._runtime = replace(
            self._runtime,
            context_window_tokens=context_window_tokens,
        )
        return self._runtime

    # 刷新provider的生成参数（如果self._tracks_provider_generation为true就修改），为什么跟随provider默认的generation修改？
    def _refresh_provider_generation(self) -> LLMRuntime | None:
        """Adopt direct provider-default changes only for provider-backed defaults."""
        if not self._tracks_provider_generation:
            return None
        runtime = self._runtime
        captured = LLMRuntime.capture(
            runtime.provider,
            runtime.model,
            context_window_tokens=runtime.context_window_tokens,
            model_preset=runtime.model_preset,
            snapshot_signature=runtime.snapshot_signature,
        )
        if captured.generation == runtime.generation:
            return None
        self._runtime = replace(runtime, generation=captured.generation)
        return self._runtime

    # 刷新默认配置，这块不是特别理解？（后面再说！！！！）
    def refresh(self) -> LLMRuntime | None:
        """Refresh configured defaults and return the replacement when changed."""
        if self._provider_snapshot_loader is None:
            self._refresh_required = False
            return None

        self._resolved_presets.clear()
        snapshot = self._provider_snapshot_loader()
        default_selection = preset_helpers.default_selection_signature(
            snapshot.signature,
            snapshot.model_preset,
        )
        active_preset = self._runtime.model_preset
        if active_preset and self._default_selection_signature in (None, default_selection):
            runtime = self.resolve_preset(active_preset)
        else:
            runtime = self.resolve_snapshot(snapshot)

        unchanged = (
            runtime.snapshot_signature == self._runtime.snapshot_signature
            and runtime.model_preset == self._runtime.model_preset
        )
        self._refresh_required = False
        if unchanged:
            self._default_selection_signature = default_selection
            return None
        (
            self._runtime,
            self._tracks_provider_generation,
            self._default_selection_signature,
        ) = (
            runtime,
            runtime.model_preset is None,
            default_selection,
        )
        return runtime

    # 某一次运行想临时使用其他模型，但不要改变默认 Runtime。
    def resolve_override(
        self,
        *,
        model: str | None,
        model_preset: str | None,
        config: Config | None = None,
    ) -> LLMRuntime | None:
        """Resolve an SDK-style per-run override without mutating the default."""
        # 不能同时制定model 和 model_preset
        if model is not None and model_preset is not None:
            raise ValueError("model and model_preset are mutually exclusive")
        # 返回一个临时runtime
        if model_preset is not None:
            return self.resolve_preset(model_preset)
        if model is None:
            return None
        # 返回一个只修改model和签名 的 runtime
        if config is None:
            return LLMRuntime(
                provider=self._runtime.provider,
                model=model,
                generation=self._runtime.generation,
                context_window_tokens=self._runtime.context_window_tokens,
                snapshot_signature=("model_override", model),
            )

        # 获取model_preset参数
        base = config.resolve_preset(self.model_preset)
        # 将model替换，provider设置成auto
        preset = base.model_copy(update={"model": model, "provider": "auto"})
        # 根据新的preset得到runtime
        return self.resolve_snapshot(build_provider_snapshot(config, preset=preset))
