import abc
import logging
from ..abc.processor import Processor


###

L = logging.getLogger(__name__)

###

class Analyzer(Processor):
	'''
		This is general analyzer interface, which can be the basement of different analyzers. 
	'''

	def __init__(self, app, pipeline, id=None, config=None):
		super().__init__(app, pipeline, id=id, config=config)

	## Implementation interface
	@abc.abstractmethod
	def predicate(self, event):
		'''
			This function is meant to check, if the event is worth to process.
			If it is, should return True.
			Specific for each analyzer.
		'''
		raise NotImplemented("")

	@abc.abstractmethod
	def analyze(self):
		'''
			The main function, which runs through the analyzed object.
			Specific for each analyzer.
		'''
		raise NotImplemented("")

	@abc.abstractmethod
	def evaluate(self, event):
		'''
			The function which records the information from the event into the analyzed object.
			Specific for each analyzer.
		'''
		raise NotImplemented("")

	def process(self, context, event):
		'''
			The event passes through `process(context, event)` unchanged.
			Meanwhile it is evaluated. 
		'''
		if self.predicate(event):
			self.evaluate(event)

		return event

