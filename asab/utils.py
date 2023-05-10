import os
import urllib.parse
import configparser


def convert_to_seconds(value: str) -> float:
	"""
	Parse time duration string (e.g. "3h", "20m" or "1y") and convert it into seconds.
	"""
	value = value.replace(" ", "")

	try:
		# Second condition in each IF is for backward compatibility
		if value.endswith("ms"):
			value = float(value[:-2]) / 1000.0
		elif value.endswith("y") or value.endswith("Y"):
			value = float(value[:-1]) * 86400 * 365
		elif value.endswith("M"):
			value = float(value[:-1]) * 86400 * 31
		elif value.endswith("w") or value.endswith("W"):
			value = float(value[:-1]) * 86400 * 7
		elif value.endswith("d") or value.endswith("D"):
			value = float(value[:-1]) * 86400
		elif value.endswith("h"):
			value = float(value[:-1]) * 3600
		elif value.endswith("m"):
			value = float(value[:-1]) * 60
		elif value.endswith("s"):
			value = float(value[:-1])
		else:
			value = float(value)
	except ValueError as e:
		raise ValueError("'{}' is not a valid time specification: {}.".format(value, e))

	return value


def string_to_boolean(value: str) -> bool:
	"""
	Convert common boolean string values (e.g. "yes" or "no") into boolean.
	"""
	if isinstance(value, bool):
		return value
	if value.lower() not in configparser.ConfigParser.BOOLEAN_STATES:
		raise ValueError("Not a boolean: {}".format(value))
	return configparser.ConfigParser.BOOLEAN_STATES[value.lower()]


def validate_url(input_url: str, scheme):
	# Remove leading and trailing whitespaces before parsing
	url = urllib.parse.urlparse(input_url.strip())

	if url.path.endswith("/"):
		url = url._replace(path=url.path[:-1])

	if scheme is None:  # Scheme doesn't get checked
		return url.geturl()
	elif isinstance(scheme, tuple):  # Supports tuple
		if url.scheme in scheme:
			return url.geturl()
	elif scheme == url.scheme:
		return url.geturl()
	else:
		if url.scheme:
			raise ValueError("'{}' has an invalid scheme: '{}'".format(url.geturl(), url.scheme))
		elif not url.scheme:
			raise ValueError("'{}' does not have a scheme".format(url.geturl()))
		else:
			raise ValueError("'{}' has an invalid scheme".format(url.geturl()))
	return url.geturl()


def running_in_container():

	if os.path.exists('/.dockerenv') and os.path.isfile('/proc/self/cgroup'):
		with open('/proc/self/cgroup', "r") as f:
			if any('docker' in line for line in f.readlines()):
				return True

	# since Ubuntu 22.04 linux kernel uses cgroups v2 which do not operate with /proc/self/cgroup file
	if os.path.isfile('/proc/self/mountinfo'):
		with open('/proc/self/mountinfo', "r") as f:
			for line in f.readlines():
				# Seek for a root filesystem
				if ' / / ' not in line:
					continue

				# Is the root filesystem runs on overlay?
				if ' overlay ' not in line:
					continue

				return True

	return False
