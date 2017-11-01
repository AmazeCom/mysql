""" environment functions """
import os

# pylint: disable=invalid-name,no-self-use,dangerous-default-value

# ---------------------------------------------------------
# misc utility functions for setting up environment

def env(key, default, environ=os.environ, fn=None):
    """
    Gets an environment variable, trims away comments and whitespace,
    and expands other environment variables.
    """
    val = environ.get(key, default)
    try:
        val = val.split('#')[0]
        val = val.strip()
        val = os.path.expandvars(val)
    except (AttributeError, IndexError):
        # just swallow AttributeErrors for non-strings
        pass
    if fn: # transformation function
        val = fn(val)
    return val

def to_flag(val):
    """
    Parse environment variable strings like "yes/no", "on/off",
    "true/false", "1/0" into a bool.
    """
    try:
        return bool(int(val))
    except ValueError:
        val = val.lower()
        if val in ('false', 'off', 'no', 'n'):
            return False
            # non-"1" or "0" string, we'll treat as truthy
        return bool(val)


# env values for keys
PRIMARY_KEY = env('PRIMARY_KEY', env('SERVICE_NAME','mysql')+'-primary')
LAST_BACKUP_KEY = env('LAST_BACKUP_KEY', 'mysql-last-backup')
BACKUP_LOCK_KEY = env('BACKUP_LOCK_KEY', 'mysql-backup-running')
LAST_BINLOG_KEY = env('LAST_BINLOG_KEY', 'mysql-last-binlog')
BACKUP_NAME = env('BACKUP_NAME', 'mysql-backup-%Y-%m-%dT%H-%M-%SZ')
BACKUP_TTL_SECS = env('BACKUP_TTL_SECS', '86400')  # every 24 hours
BACKUP_FORMAT = env('BACKUP_FORMAT', 'xbstream')
BACKUP_DECOMPRESS_THREADS = env('BACKUP_DECOMPRESS_THREADS', '4')
MYSQL_USER_CONF = env('MYSQL_USER_CONF', '')  # format: key1=value1,key2=value2
