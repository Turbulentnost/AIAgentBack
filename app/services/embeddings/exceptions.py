from __future__ import annotations


class EmbeddingError(RuntimeError):
    pass


class EmptyTextEmbeddingError(EmbeddingError):
    pass


class TextTooLongEmbeddingError(EmbeddingError):
    pass


class EmbeddingProviderUnavailableError(EmbeddingError):
    pass


class EmbeddingVectorSizeMismatchError(EmbeddingError):
    pass


class EmbeddingBatchError(EmbeddingError):
    pass


class EmbeddingConfigurationError(EmbeddingError):
    pass
