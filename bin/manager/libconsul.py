""" Module for Consul client wrapper and related tooling. """
import os
import time

from manager.utils import debug, env, log, to_flag, \
    WaitTimeoutError, UnknownPrimary, PRIMARY_KEY, LAST_BACKUP_KEY

# pylint: disable=import-error,invalid-name,dangerous-default-value
import consul as pyconsul

SESSION_CACHE_FILE = env('SESSION_CACHE_FILE', '/tmp/mysql-session')
SESSION_NAME = env('SESSION_NAME', 'mysql-primary-lock')
SESSION_TTL = env('SESSION_TTL', 25, fn=int)
FAILOVER_KEY = env('FAILOVER_IN_PROGRESS', 'FAILOVER_IN_PROGRESS')
FAILOVER_SESSION_FILE = env('FAILOVER_SESSION_FILE', '/tmp/failover-session')


class Consul(object):
    """ Consul represents the Consul instance this node talks to """

    def __init__(self, envs=os.environ):
        """
        Figures out the Consul client hostname based on whether or
        not we're using a local Consul agent.
        """
        if env('CONSUL_AGENT', False, envs, fn=to_flag):
            self.host = 'localhost'
        else:
            self.host = env('CONSUL', 'consul', envs)
        self.client = pyconsul.Consul(host=self.host)

    def get(self, key):
        """
        Return the Value field for a given Consul key.
        Handles None results safely but lets all other exceptions
        just bubble up.
        """
        result = self.client.kv.get(key)
        if result[1]:
            return result[1]['Value']
        return None

    def put(self, key, value):
        """ Puts a value for the key; allows all exceptions to bubble up """
        return self.client.kv.put(key, value)

    def register_check(self, key, ttl):
        """ Registers a new health check """
        self.client.agent.check.register(
            name=key,
            check=pyconsul.Check.ttl(ttl),
            check_id=key
        )

    def pass_check(self, key):
        """ Marks an existing check as passing """
        return self.client.agent.check.ttl_pass(key)

    def is_check_healthy(self, key):
        """ Returns whether the check for the given key is passing """
        try:
            check = self.client.agent.checks()[key]
            if check['Status'] == 'passing':
                return True
            return False
        except KeyError:
            return False

    @debug(log_output=True)
    def get_session(self, key=SESSION_NAME, ttl=SESSION_TTL,
                    on_disk=SESSION_CACHE_FILE, cached=True):
        """
        Gets a Consul session ID from the on-disk cache or calls into
        `create_session` to generate a new one.
        We can't rely on storing Consul session IDs in memory because
        handler calls happen in subsequent processes. Here we create a
        session on Consul and cache the session ID to disk.
        Returns the session ID.
        """
        if not cached:
            return self.create_session(key, ttl)
        try:
            with open(on_disk, 'r') as f:
                session_id = f.read()
        except IOError:
            session_id = self.create_session(key, ttl)
        if cached:
            with open(on_disk, 'w') as f:
                f.write(session_id)

        return session_id

    @debug(log_output=True)
    def create_session(self, key, ttl=120):
        """ Create a session on Consul and return the session ID """
        return self.client.session.create(name=key,
                                          behavior='release',
                                          ttl=ttl)

    @debug(log_output=True)
    def renew_session(self, session_id=None):
        """ Renews the session TTL on Consul """
        if not session_id:
            session_id = self.get_session()
        self.client.session.renew(session_id)

    @debug(log_output=True)
    def lock(self, key, value, session_id):
        """ Puts a key to Consul with an advisory lock """
        return self.client.kv.put(key, value, acquire=session_id)

    @debug
    def unlock(self, key, session_id):
        """ Clears a key in Consul and its advisory lock """
        return self.client.kv.put(key, "", release=session_id)

    @debug(log_output=True)
    def is_locked(self, key):
        """
        Checks a lock in Consul and returns the session_id if the
        lock is still valid, otherwise False
        """
        lock = self.client.kv.get(key)
        try:
            session_lock = lock[1]['Session']
            return session_lock
        except KeyError:
            return False

    @debug(log_output=True)
    def read_lock(self, key):
        """
        Checks a lock in Consul and returns the (session_id, value) if the
        lock is still valid, otherwise (None, None)
        """
        lock = self.client.kv.get(key)
        try:
            session_lock = lock[1]['Session']
            value = lock[1]['Value']
            return session_lock, value
        except KeyError:
            return None, None

    @debug(log_output=True)
    def has_snapshot(self, timeout=60):
        """ Ask Consul for 'last backup' key. """
        while timeout > 0:
            try:
                result = self.client.kv.get(LAST_BACKUP_KEY)
                if result[1]:
                    return result[1]['Value']
                return None
            except pyconsul.ConsulException:
                # Consul isn't up yet
                timeout -= 1
                time.sleep(1)
        raise WaitTimeoutError('Could not contact Consul to check '
                               'for snapshot after %s seconds', timeout)

    @debug(log_output=True)
    def get_primary(self, timeout=10):
        """
        Returns the (name, IP) tuple for the instance that Consul thinks
        is the healthy primary.
        """
        while timeout > 0:
            try:
                nodes = self.client.health.service(PRIMARY_KEY, passing=True)[1]
                log.debug(nodes)
                instances = [service['Service'] for service in nodes]
                if len(instances) > 1:
                    raise UnknownPrimary('Multiple primaries detected! %s', instances)
                return instances[0]['ID'], instances[0]['Address']
            except pyconsul.ConsulException as ex:
                log.debug(ex)
                timeout = timeout - 1
                time.sleep(1)
            except (IndexError, KeyError):
                raise UnknownPrimary('No primary found')
        raise WaitTimeoutError('Could not find primary before timeout.')

    @debug
    def mark_as_primary(self, name):
        """ Write flag to Consul to mark this node as primary """
        session_id = self.get_session()
        if not self.lock(PRIMARY_KEY, name, session_id):
            return False
        return session_id

    @debug
    def lock_failover(self, hostname):
        """
        Lock a session in Consul for the failover and cache the
        session as a file on disk.
        """
        session_id = self.get_session(FAILOVER_KEY, ttl=120,
                                      on_disk=FAILOVER_SESSION_FILE)
        return self.lock(FAILOVER_KEY, hostname, session_id)

    @debug
    def wait_for_failover_lock(self):
        """
        Block forever waiting on the session lock on the
        failover to complete.
        """
        while True:
            if not self.is_locked(FAILOVER_KEY):
                break
            time.sleep(3)

    @debug
    def unlock_failover(self):
        """
        If we've previously locked a session for failover and a new
        primary has registered as healthy, unlock the session and
        remove the session file.
        """
        try:
            with open(FAILOVER_SESSION_FILE, 'r') as f:
                session_id = f.read()
                if self.get_primary():
                    self.unlock(FAILOVER_KEY, session_id)
                    os.remove(FAILOVER_SESSION_FILE)
        except (IOError, OSError):
            # we don't have a session file so just move on
            pass
        except (UnknownPrimary, WaitTimeoutError):
            # the primary isn't ready yet so we'll try
            # to unlock again on the next pass
            log.debug('failover session lock (%s) not removed because '
                      'primary has not reported as healthy', session_id)