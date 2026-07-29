import re
import spacy
from keybert import KeyBERT

# Load models once (application startup)
nlp = spacy.load("en_core_web_sm")
kw_model = KeyBERT(model="all-MiniLM-L6-v2")


def extract_hashtags(text: str) -> list[str]:
    hashtags = re.findall(r"#([A-Za-z0-9_]+)", text)

    # Remove duplicates while preserving order
    return list(dict.fromkeys(hashtags))


def remove_hashtags(text: str) -> str:
    """
    Remove hashtags before preprocessing.
    """
    return re.sub(r"#\w+", "", text)


def preprocess_text(text: str) -> str:
    """
    Remove stop words, punctuation, numbers and lemmatize.
    """

    doc = nlp(text)

    tokens = [
        token.lemma_.lower()
        for token in doc
        if not token.is_stop
        and not token.is_punct
        and not token.like_num
        and token.is_alpha
    ]

    return " ".join(tokens)


def extract_keywords(processed_text: str) -> list[tuple[str, float]]:
    """
    Extract keywords using KeyBERT, then apply post-processing cleanup.

    Returns cleaned (keyword, score) tuples sorted by score descending.
    """
    from apps.recommendation.services.keyword_postprocessing import clean_keywords

    raw_keywords = kw_model.extract_keywords(
        processed_text,
        keyphrase_ngram_range=(1, 3),
        top_n=10,
        stop_words=None,      # Already removed by spaCy
        use_maxsum=True,
        nr_candidates=20,
    )

    return clean_keywords(list(raw_keywords))


def extract_post_keywords(text: str) -> dict:
    """
    Complete pipeline:
    1. Extract hashtags
    2. Remove hashtags
    3. Preprocess text
    4. Extract keywords using KeyBERT
    5. Post-process keyword candidates
    """

    hashtags = extract_hashtags(text)

    cleaned_text = remove_hashtags(text)

    processed_text = preprocess_text(cleaned_text)

    keywords_with_scores = extract_keywords(processed_text)

    return {
        "processed_text": processed_text,
        "hashtags": hashtags,
        "keywords": [keyword for keyword, _ in keywords_with_scores],
        "keywords_with_scores": keywords_with_scores,
    }


if __name__ == "__main__":

    text = """
    I am currently learning FastAPI, Python, Machine Learning,
    Natural Language Processing and Large Language Models.
    I love building AI applications with Semantic Search.
    #Python #MachineLearning #FastAPI #AI
    """

    result = extract_post_keywords(text)

    print("Processed Text:")
    print(result["processed_text"])

    print("\nHashtags:")
    print(result["hashtags"])

    print("\nKeywords:")
    print(result["keywords"])