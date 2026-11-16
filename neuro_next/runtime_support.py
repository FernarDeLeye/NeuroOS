from .installation import installed
from .user_systemd import UserSystemd
from .portable_resources import Resources

def unit_environment_args():
 if not installed():return []
 from .installation import config_path
 path=str(config_path().resolve())
 if any(c in path for c in '\n\r%$'):raise ValueError('Unsupported systemd config path characters')
 return ['--setenv=NEUROOS_CONFIG='+path]
