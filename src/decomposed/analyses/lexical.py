"""Lambda especializada: analise lexical.

Somente stdlib via common.analysis; sem boto3 e sem DynamoDB (RNF-04).
"""

from common.analysis import lexical_analysis


def handler(event, context) -> dict:
    return lexical_analysis(event["body"], event["language"], event["tags"])