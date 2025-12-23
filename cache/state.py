import time
import json
import threading
import os
from typing import Dict, Optional, Any
import datetime


class RedisStateManager:
    """
    Thread-safe in-memory state manager Redis-like.
    """

    # Class variables shared across all instances
    _store: Dict[str, Dict[str, Any]] = {}
    _lock = threading.RLock()  # Reentrant lock for thread safety
    _cleanup_running = False

    def __init__(self):
        self.app_name = os.getenv("REDIS_APP_KEY", "sentient_claude")

    def _make_key(self, key_type: str, *parts: str) -> str:
        """Generate namespaced key"""
        return f"{self.app_name}:{key_type}:" + ":".join(parts)

    def _set_with_ttl(self, key: str, value: Any, ttl: int):
        """Set value with expiration timestamp"""
        expiry = time.time() + ttl
        with self._lock:
            self._store[key] = {"value": value, "expiry": expiry}

    def _get(self, key: str) -> Any:
        """Get value if not expired"""
        with self._lock:
            if key not in self._store:
                return None

            entry = self._store[key]
            # Check expiry
            if time.time() > entry["expiry"]:
                del self._store[key]
                return None

            return entry["value"]

    def _delete(self, key: str):
        """Delete key"""
        with self._lock:
            self._store.pop(key, None)

    # ========================= Streaming States =========================

    def set_streaming_state(
        self, claude_id: str, stream_id: str, active: bool = True
    ) -> None:
        try:
            key = self._make_key("streaming", claude_id, stream_id)
            self._set_with_ttl(key, json.dumps({"active": active}), 3600)
        except Exception as e:
            print(f"State error in set_streaming_state: {e}")

    def get_streaming_state(self, claude_id: str, stream_id: str) -> bool:
        try:
            key = self._make_key("streaming", claude_id, stream_id)
            data = self._get(key)
            if data:
                return json.loads(data)["active"]
            return True
        except (json.JSONDecodeError, Exception) as e:
            print(f"State error in get_streaming_state: {e}")
            return True

    # ========================= Journal =========================

    def set_journal(self, claude_id: str, notes: str, feelings: str) -> None:
        """Journal notes, findings, feelings"""
        try:
            key = self._make_key("journal", claude_id)
            journal = {"notes": notes, "feelings": feelings}
            # Store as JSON string so _get returns parseable data
            self._set_with_ttl(key, json.dumps(journal), 3600)
        except Exception as e:
            print(f"State error in setting journal: {e}")

    def get_journal(self, claude_id: str) -> str:
        """Get the journal to claude"""
        try:
            key = self._make_key("journal", claude_id)
            return self._get(key)
        except Exception as e:
            print(f"State error in get_journal: {e}")
            return None

    # ========================= Stimuli =========================

    def add_stimulus(
        self, claude_id: str, content: str, source: str, energy_level: str = None
    ) -> None:
        """Add stimulus to queue for claude"""
        stimulus = {
            "content": content,
            "source": source,  # "circadian" | "user"
            "energy_level": energy_level,
            "timestamp": datetime.datetime.now().isoformat(),  # Fix: datetime.datetime
        }
        try:
            key = self._make_key("stimuli", claude_id)
            # Get existing stimuli or empty list
            existing_data = self._get(key)
            pending_stimuli = json.loads(existing_data) if existing_data else []
            # Append new stimulus
            pending_stimuli.append(stimulus)
            # Store back as JSON string
            self._set_with_ttl(key, json.dumps(pending_stimuli), 3600)
        except Exception as e:
            print(f"State error in add_stimulus: {e}")

    def get_pending_stimuli(self, claude_id: str) -> list:
        """Get pending stimuli and clear queue"""
        try:
            key = self._make_key("stimuli", claude_id)
            data = self._get(key)
            if data:
                stimuli = json.loads(data)
                self._delete(key)  # Clear after reading
                return stimuli
            return []
        except Exception as e:
            print(f"Error in get_pending_stimuli: {e}")
            return []

    # ========================= CLAUDE TIME =========================

    def init_claude_time(self, claude_id: str, time_scale: int = 60):
        """Initialize Claude's internal clock"""
        time_data = {
            "start_time": datetime.datetime.now().isoformat(),  # Fix: datetime.datetime
            "time_scale": time_scale,  # 1 real minute = 1 Claude hour
            "current_hour": 6,  # Start at 6am
        }
        key = self._make_key("time", claude_id)
        # Store as JSON with 24 hour TTL
        self._set_with_ttl(key, json.dumps(time_data), 86400)

    def get_claude_hour(self, claude_id: str) -> int:
        """Get Claude's current hour (0-23)"""
        key = self._make_key("time", claude_id)
        data = self._get(key)  # Fix: use _get method
        if not data:
            return 6

        time_data = json.loads(data)
        start = datetime.datetime.fromisoformat(
            time_data["start_time"]
        )  # Fix: datetime.datetime
        elapsed_real_minutes = (
            datetime.datetime.now() - start
        ).total_seconds() / 60  # Fix: datetime.datetime
        elapsed_claude_hours = int(elapsed_real_minutes)  # 1 minute = 1 hour

        current_hour = (time_data["current_hour"] + elapsed_claude_hours) % 24
        return current_hour

    # ========================= Kernel Lock =========================

    def acquire_kernel_lock(self, claude_id: str, timeout: int = 30) -> bool:
        """Acquire exclusive lock for kernel operations (atomic)"""
        try:
            lock_key = self._make_key("kernel_lock", claude_id)
            with self._lock:
                # Atomic check-and-set (mimics Redis nx=True)
                if lock_key in self._store:
                    entry = self._store[lock_key]
                    if time.time() <= entry["expiry"]:
                        return False  # Lock exists and not expired

                    # Lock expired, clean up
                    del self._store[lock_key]

                # Acquire lock
                self._set_with_ttl(lock_key, "locked", timeout)
                return True
        except Exception as e:
            print(f"State error in acquire_kernel_lock: {e}")
            return False

    def release_kernel_lock(self, claude_id: str):
        """Release kernel lock"""
        try:
            lock_key = self._make_key("kernel_lock", claude_id)
            self._delete(lock_key)
        except Exception as e:
            print(f"State error in release_kernel_lock: {e}")

    # ========================= Kernel TTL Tracking =========================

    def extend_kernel_ttl(self, claude_id: str, seconds: int = 120):
        """Set/extend TTL for user kernel tracking"""
        try:
            key = self._make_key("kernel", claude_id)
            self._set_with_ttl(key, "active", seconds)
        except Exception as e:
            print(f"State error in extend_kernel_ttl: {e}")

    def get_all_kernel_users_with_ttl(self) -> Dict[str, int]:
        """Get all users with active kernel TTL keys and their remaining time"""
        try:
            pattern_prefix = self._make_key("kernel", "")
            result = {}

            with self._lock:
                current_time = time.time()
                for key, entry in self._store.items():
                    if key.startswith(pattern_prefix) and not "lock" in key:
                        user_id = key.split(":")[-1]
                        ttl = int(entry["expiry"] - current_time)
                        if ttl > 0:
                            result[user_id] = ttl

            return result
        except Exception as e:
            print(f"State error in get_all_kernel_users_with_ttl: {e}")
            return {}

    # ========================= Kernel PID Storage =========================

    def set_kernel_pid(self, claude_id: str, pid: int):
        """Store kernel PID for cross-worker cleanup"""
        try:
            key = self._make_key("kernel_pid", claude_id)
            self._set_with_ttl(key, str(pid), 300)  # 5 minutes expiry
        except Exception as e:
            print(f"State error in set_kernel_pid: {e}")

    def get_kernel_pid(self, claude_id: str) -> Optional[int]:
        """Get kernel PID"""
        try:
            key = self._make_key("kernel_pid", claude_id)
            pid_str = self._get(key)
            if pid_str:
                return int(pid_str)
            return None
        except (ValueError, Exception) as e:
            print(f"State error in get_kernel_pid: {e}")
            return None

    def delete_kernel_pid(self, claude_id: str):
        """Delete kernel PID after cleanup"""
        try:
            key = self._make_key("kernel_pid", claude_id)
            self._delete(key)
        except Exception as e:
            print(f"State error in delete_kernel_pid: {e}")
