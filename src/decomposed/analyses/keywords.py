"""Lambda especializada: extracao de palavras-chave.

Somente stdlib via common.analysis; sem boto3 e sem DynamoDB (RNF-04).
"""

from common.analysis import keyword_analysis


def handler(event, context) -> dict:
    return keyword_analysis(event["body"], event["language"], event["tags"])