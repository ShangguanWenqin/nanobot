"""Integrity-checked atomic cache publication for local RAG models."""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from importlib import import_module
from pathlib import Path
from typing import Protocol, cast

from nanobot.rag.model_manifest import LocalModelManifest, ModelArtifact
from nanobot.rag.types import RagErrorCode


class ArtifactDownloader(Protocol):
    def download(
        self,
        repository: str,
        revision: str,
        remote_path: str,
        destination: Path,
    ) -> None: ...


class HuggingFaceDownloadFunction(Protocol):
    def __call__(
        self,
        *,
        repo_id: str,
        revision: str,
        filename: str,
    ) -> str: ...


class HuggingFaceDownloader:
    """Download only explicitly named files from an immutable repository revision."""

    def __init__(
        self,
        download_function: HuggingFaceDownloadFunction | None = None,
    ) -> None:
        self._download_function = download_function

    def download(
        self,
        repository: str,
        revision: str,
        remote_path: str,
        destination: Path,
    ) -> None:
        download = self._download_function
        if download is None:
            module = import_module("huggingface_hub")
            download = cast(
                HuggingFaceDownloadFunction,
                getattr(module, "hf_hub_download"),
            )
        try:
            source = Path(
                download(
                    repo_id=repository,
                    revision=revision,
                    filename=remote_path,
                )
            )
            if not source.is_file():
                raise OSError("downloaded model artifact is not a regular file")
            shutil.copyfile(source, destination)
        except Exception as exc:
            if isinstance(exc, OSError):
                raise
            raise OSError("Hugging Face artifact download failed") from exc


class ModelCacheError(RuntimeError):
    def __init__(self, code: RagErrorCode, safe_message: str) -> None:
        self.code = code
        self.safe_message = safe_message
        super().__init__(safe_message)


class ModelCache:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).expanduser().absolute()
        if self.root.exists() and self.root.is_symlink():
            raise ValueError("model cache root must not be a symbolic link")
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.root.chmod(0o700)

    def prepare(
        self,
        manifest: LocalModelManifest,
        downloader: ArtifactDownloader,
        *,
        offline: bool = False,
    ) -> Path:
        from filelock import FileLock

        target = self.root / manifest.profile_id
        if self.verify(manifest):
            return target
        if target.exists():
            raise ModelCacheError(
                RagErrorCode.MODEL_INTEGRITY_FAILED,
                "本地模型缓存完整性校验失败",
            )
        if offline:
            raise ModelCacheError(RagErrorCode.MODEL_MISSING, "离线模式下缺少本地模型")

        lock_path = self.root / f"{manifest.profile_id}.lock"
        with FileLock(lock_path, timeout=300):
            if self.verify(manifest):
                return target
            if target.exists():
                raise ModelCacheError(
                    RagErrorCode.MODEL_INTEGRITY_FAILED,
                    "本地模型缓存完整性校验失败",
                )
            temporary = Path(
                tempfile.mkdtemp(
                    prefix=f"{manifest.profile_id}.partial-",
                    dir=self.root,
                )
            )
            try:
                for artifact in manifest.artifacts:
                    destination = temporary / artifact.path
                    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    try:
                        downloader.download(
                            manifest.repository,
                            manifest.revision,
                            artifact.path,
                            destination,
                        )
                    except OSError as exc:
                        raise ModelCacheError(
                            RagErrorCode.MODEL_MISSING,
                            "本地模型文件下载失败",
                        ) from exc
                    self._verify_artifact(destination, artifact)
                    destination.chmod(0o600)
                manifest_path = temporary / "manifest.json"
                manifest_path.write_text(manifest.canonical_json(), encoding="utf-8")
                manifest_path.chmod(0o600)
                os.replace(temporary, target)
                target.chmod(0o700)
            finally:
                if temporary.exists():
                    self._remove_temporary_directory(temporary)
        if not self.verify(manifest):
            raise ModelCacheError(
                RagErrorCode.MODEL_INTEGRITY_FAILED,
                "发布后的本地模型缓存完整性校验失败",
            )
        return target

    def prefetch(
        self,
        manifest: LocalModelManifest,
        downloader: ArtifactDownloader | None = None,
    ) -> Path:
        """Explicit deployment/admin entry point for preparing an offline cache."""

        return self.prepare(manifest, downloader or HuggingFaceDownloader())

    def verify(self, manifest: LocalModelManifest) -> bool:
        target = self.root / manifest.profile_id
        if not target.is_dir() or target.is_symlink():
            return False
        manifest_path = target / "manifest.json"
        try:
            if manifest_path.read_text(encoding="utf-8") != manifest.canonical_json():
                return False
            return all(
                self._artifact_matches(target / artifact.path, artifact)
                for artifact in manifest.artifacts
            )
        except OSError:
            return False

    @staticmethod
    def _artifact_matches(path: Path, artifact: ModelArtifact) -> bool:
        if not path.is_file() or path.is_symlink():
            return False
        try:
            if path.stat().st_size != artifact.bytes:
                return False
            digest = hashlib.sha256()
            with path.open("rb") as stream:
                while chunk := stream.read(1024 * 1024):
                    digest.update(chunk)
            return digest.hexdigest() == artifact.sha256
        except OSError:
            return False

    @classmethod
    def _verify_artifact(cls, path: Path, artifact: ModelArtifact) -> None:
        if not cls._artifact_matches(path, artifact):
            raise ModelCacheError(
                RagErrorCode.MODEL_INTEGRITY_FAILED,
                "下载的模型文件完整性校验失败",
            )

    def _remove_temporary_directory(self, path: Path) -> None:
        if path.is_symlink() or path.parent != self.root or ".partial-" not in path.name:
            raise ModelCacheError(
                RagErrorCode.MODEL_INTEGRITY_FAILED,
                "拒绝清理不安全的模型临时目录",
            )
        shutil.rmtree(path)


__all__ = [
    "ArtifactDownloader",
    "HuggingFaceDownloader",
    "HuggingFaceDownloadFunction",
    "ModelCache",
    "ModelCacheError",
]
