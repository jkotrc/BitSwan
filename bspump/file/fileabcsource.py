import abc
import os
import logging
import asyncio
import asab

from ..abc.source import Source
from .. import ProcessingError

from .globscan import _glob_scan

#

L = logging.getLogger(__file__)

#

class FileABCSource(Source):


	ConfigDefaults = {
		'path': '',
		'mode': 'rb',
		'post': "stop", # one of 'delete', 'stop' and 'move'
	}


	def __init__(self, app, pipeline, id=None, config=None):
		super().__init__(app, pipeline, id=id, config=config)
		self.App = app
		self.Loop = app.Loop
		self._future = None
		app.PubSub.subscribe("Application.tick/10!", self._on_health_check)

		self.path = self.Config['path']
		self.mode = self.Config['mode']
		self.post = self.Config['post']
		if self.post not in ['delete', 'stop', 'move']:
			L.warning("Incorrect/unknown 'post' configuration value '{}' - defaulting to 'move'".format(self.post))
			self.post = 'move'


	@abc.abstractmethod
	async def read(self, filename, f):
		'''
		Override this method to implement your File Source.
		`f` is an opened file object.
		'''
		raise NotImplemented()


	async def main(self):
		self._on_health_check('pipeline.started!')


	def _on_health_check(self, message_type):
		if self._future is not None:
			if not self._future.done():
				# We are still processing a file
				return

			try:
				self._future.result()
			except:
				L.exception("Unexpected error when reading file")

			self._future = None

		assert(self._future is None)

		filename = _glob_scan(self.path)
		if filename is None: return # No file to read

		self._future = asyncio.ensure_future(
			self._read_file(filename, self.mode),
			loop=self.Loop
		)


	async def _read_file(self, filename, mode):
		await self.Pipeline.ready()

		# Lock the file
		L.debug("Locking file '{}'".format(filename))
		locked_filename = filename + '-locked'
		try:
			os.rename(filename, locked_filename)
		except FileNotFoundError:
			# Lock failed (gracefully), abort and try to start again
			self.Loop.call_soon(self._on_health_check, 'file.read!')
			return
		except Exception as e:
			L.exception("Error when locking the file '{}'".format(filename))
			self.Pipeline.set_error(e, None)
			return

		try:
			if filename.endswith(".gz"):
				import gzip
				f = gzip.open(locked_filename, mode)

			elif filename.endswith(".bz2"):
				import bz2
				f = bz2.open(locked_filename, mode)

			elif filename.endswith(".xz") or filename.endswith(".lzma"):
				import lzma
				f = lzma.open(locked_filename, mode)

			else:
				f = open(locked_filename, mode)

		except:
			self.Pipeline.set_error(ProcessingError("The file '{}' could not be read.".format(filename)), None)
			return

		L.debug("Processing file '{}'".format(filename))

		try:
			await self.read(filename, f)
		except:
			try:
				if self.post == "stop":
					# When we should stop, rename file back to original
					os.rename(locked_filename, filename)
				else:
					# Otherwise rename to ...-failed and continue processing
					os.rename(locked_filename, filename + '-failed')
			except:
				L.exception("Error when finalizing the file '{}'".format(filename))
			raise
		finally:
			f.close()

		L.debug("File '{}' processed {}".format(filename, "succefully"))
		self.Pipeline.flush()

		# Finalize
		try:
			if self.post == "delete":
				os.unlink(locked_filename)
			elif self.post == "stop":
				os.rename(locked_filename, filename)
				self.App.stop()
				return
			else:
				os.rename(locked_filename, filename + '-processed')
		except Exception as e:
			L.exception("Error when finalizing the file '{}'".format(filename))
			self.Pipeline.set_error(e, None)
			return

		# Ensure that we iterate to a next file quickly
		self.Loop.call_soon(self._on_health_check, 'file.read!')

