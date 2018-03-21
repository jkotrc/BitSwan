import abc
from .abc.config import ConfigObject

class ProcessorBase(abc.ABC, ConfigObject):


	def __init__(self, app, pipeline, id=None, config=None):
		super().__init__("pipeline:{}:{}".format(pipeline.Id, id if id is not None else self.__class__.__name__), config=config)

		self.Id = id if id is not None else self.__class__.__name__
		self.Pipeline = pipeline


	@abc.abstractmethod
	def process(self, event):
		raise NotImplemented()


	def start(self):
		'''
		Override this to handle request to start
		'''
		pass


	def flush(self):
		'''
		Override this to handle request to flush all buffers
		'''
		pass


class Source(abc.ABC, ConfigObject):


	def __init__(self, app, pipeline, id=None, config=None):
		super().__init__("pipeline:{}:{}".format(pipeline.Id, id if id is not None else self.__class__.__name__), config=config)

		self.Id = id if id is not None else self.__class__.__name__
		self.Pipeline = pipeline


	def process(self, event):
		if not self.Pipeline._ready.is_set():
			raise RuntimeError("Pipeline is not ready to process events")
		return self.Pipeline.process(event)


	@abc.abstractmethod
	async def start(self):
		raise NotImplemented()


class Processor(ProcessorBase):
	pass


class Sink(ProcessorBase):
	pass


class Generator(ProcessorBase):
	'''
	Example of use:

	class GeneratingProcessor(bspump.Generator):

		def process(self, event):

			def generate(items):
				for item in items:
					yield item

			return generate(event.items)
	'''
	pass
