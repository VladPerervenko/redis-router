import ketama
import redis
import re
import logging


class Router(object):

    SERVERS = {}
    METHOD_BLACKLIST = [
        'smove',  # it's hard to shard with atomic approach.
    ]

    def __init__(self, ketama_server_file):
        self.server_list = self.parse_server_file(ketama_server_file)
        self.continuum = ketama.Continuum(ketama_server_file)
        for hostname, port in self.server_list:
            server_string = "{0}:{1}".format(hostname, port)

            # creating a emtpy record for lazy connection responses.
            self.SERVERS.update({
                server_string: None,
                })

    def strict_connection(self, hostname, port):
        if not isinstance(port, int):
            try:
                port = int(port)
            except ValueError:
                raise ValueError('port must be int or int convertable.')

        return redis.StrictRedis(host=hostname, port=port, db=0)

    def get_connection(self, key):
        key_hash, connection_uri = self.continuum.get_server(key)
        hostname, port = connection_uri.split(":")

        logging.debug("key '{0}' hashed as {1} and mapped to {2}".format(
            key,
            key_hash,
            connection_uri
        ))

        connection = self.SERVERS.get(connection_uri)
        if not connection:
            self.SERVERS.update({
                connection_uri: self.strict_connection(hostname, port),
                })

            connection = self.SERVERS.get(connection_uri)

        return connection

    def __getattr__(self, methodname):

        def method(*args, **kwargs):
            if len(args) < 1:
                raise AttributeError("not enough arguments.")

            connection = self.get_connection(args[0])

            if hasattr(connection, methodname):
                return getattr(connection, methodname)(*args, **kwargs)
            else:
                raise AttributeError("invalid method name:{0}".format(methodname))

        return method

    def __set_generator(self, *args):
        """
        iterable for the custom set methods: ["sinter", "sdiff", "sunion"]
        returns related set's members as python's built-in set.
        """
        for index, key in enumerate(args):
            yield set(self.smembers(key))

    def sinter(self, *args):
        return list(set.intersection(*self.__set_generator(*args)))

    def sinterstore(self, destination, *args):
        intersection = self.sinter(*args)
        if len(intersection) > 0:
            self.sadd(destination, *intersection)

        return len(intersection)

    def sdiff(self, *args):
        return list(set.difference(*self.__set_generator(*args)))

    def sdiffstore(self, destination, *args):
        difference = self.sdiff(*args)
        if len(difference) > 0:
            self.sadd(destination, *difference)

        return len(difference)

    def sunion(self, *args):
        return list(set.union(*self.__set_generator(*args)))

    def sunionstore(self, destination, *args):
        union = self.sunion(*args)
        if len(union) > 0:
            return self.sadd(destination, *union)

        return len(union)

    def ping_all(self):
        """
        pings all shards and returns the results.
        raises a redis.exceptions.ConnectionError if a shard is down.
        """
        results = list()
        for connection_uri, connection in self.SERVERS.items():
            if not connection:
                connection = self.strict_connection(*connection_uri.split(":"))

            results.append({
                "result": connection.ping(),
                "connection_uri": connection_uri,
                })

        return results

    def dbsize(self):
        """
        returns the number of keys across all the shards.
        """
        result = 0
        for connection_uri, connection in self.SERVERS.items():
            if not connection:
                connection = self.strict_connection(*connection_uri.split(":"))

            result += int(connection.dbsize())

        return result

    def parse_server_file(self, ketama_server_file):
        file_content = open(ketama_server_file).read()
        result = re.findall('([^:]*):([^\s]*)\s[^\n]*\n', file_content)

        return result

