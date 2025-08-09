import os
import json
from typing import Dict
from pathlib import Path


app_name = "clash-auto-switch"


def get_data_directory() -> Path:
    """Get the appropriate data directory for the current OS."""
    if os.name == 'nt':  # Windows
        app_data = os.environ.get('APPDATA', os.path.expanduser('~'))
        data_dir = Path(app_data) / app_name
    elif os.name == 'posix':  # Unix/Linux/macOS
        if 'darwin' in os.uname().sysname.lower():  # macOS
            data_dir = Path.home() / 'Library' / 'Application Support' / app_name
        else:  # Linux
            xdg_data_home = os.environ.get('XDG_DATA_HOME', str(Path.home() / '.local' / 'share'))
            data_dir = Path(xdg_data_home) / app_name
    else:
        # Fallback to user home directory
        data_dir = Path.home() / f'.{app_name}'
    return data_dir


def get_data_file_path() -> Path:
    """Get the node history data file path."""
    return get_data_directory() / 'node_history.json'


def get_config_file_path() -> Path:
    """Get the configuration file path."""
    return get_data_directory() / 'config.json'


def load_config() -> Dict:
    """Load configuration from the standard config file location."""
    config_file = get_config_file_path()
    if not config_file.exists():
        return {}

    try:
        with config_file.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        print(f"警告: 配置文件读取失败 ({config_file}): {e}")
        return {}


def save_config(config_data: Dict) -> bool:
    """Save configuration to the standard config file location."""
    config_file = get_config_file_path()
    config_file.parent.mkdir(parents=True, exist_ok=True)

    try:
        with config_file.open("w", encoding="utf-8") as f:
            json.dump(config_data, f, indent=2, ensure_ascii=False)
        return True
    except IOError as e:
        print(f"错误: 配置文件保存失败 ({config_file}): {e}")
        return False


def has_config() -> bool:
    """Check if configuration file exists."""
    return get_config_file_path().exists()
