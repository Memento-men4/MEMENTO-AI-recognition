import json
import os
import pickle
import sys
import time
from contextlib import contextmanager
from typing import List, NoReturn, Optional, Tuple, Union

import numpy as np
import pandas as pd
from datasets import Dataset, concatenate_datasets, load_from_disk
from sklearn.feature_extraction.text import TfidfVectorizer
from tqdm.auto import tqdm
from elastic_setting import *

sys.path.append("model")


@contextmanager
def timer(name):
    t0 = time.time()
    yield
    print(f"[{name}] done in {time.time() - t0:.3f} s")


class SparseRetrieval:
    def __init__(
        self,
        tokenize_fn,
        data_path: Optional[str] = "/opt/ml/input/data",
        context_path: Optional[str] = "../data/total_meeting_collection.json",
    ) -> NoReturn:

        self.data_path = data_path
        with open(
            os.path.join(data_path, context_path), "r", encoding="utf-8"
        ) as f:
            wiki = json.load(f)

        self.contexts = list(
            dict.fromkeys([v["text"] for v in wiki.values()])
        )  # set 은 매번 순서가 바뀌므로
        print(f"Lengths of unique contexts : {len(self.contexts)}")
        self.ids = list(range(len(self.contexts)))

        # Transform by vectorizer
        self.tfidfv = TfidfVectorizer(
            tokenizer=tokenize_fn,
            ngram_range=(1, 2),
            max_features=50000,
        )

        self.p_embedding = None  # get_sparse_embedding()로 생성합니다
        self.indexer = None  # build_faiss()로 생성합니다.

    def get_sparse_embedding(self) -> NoReturn:

        # Pickle을 저장
        pickle_name = f"sparse_embedding.bin"
        tfidfv_name = f"tfidv.bin"
        emd_path = os.path.join(self.data_path, pickle_name)
        tfidfv_path = os.path.join(self.data_path, tfidfv_name)

        if os.path.isfile(emd_path) and os.path.isfile(tfidfv_path):
            with open(emd_path, "rb") as file:
                self.p_embedding = pickle.load(file)
            with open(tfidfv_path, "rb") as file:
                self.tfidfv = pickle.load(file)
            print("Embedding pickle load.")
        else:
            print("Build passage embedding")
            self.p_embedding = self.tfidfv.fit_transform(self.contexts)
            print(self.p_embedding.shape)
            with open(emd_path, "wb") as file:
                pickle.dump(self.p_embedding, file)
            with open(tfidfv_path, "wb") as file:
                pickle.dump(self.tfidfv, file)
            print("Embedding pickle saved.")

    def build_faiss(self, num_clusters=64) -> NoReturn:

    def retrieve(
        self, query_or_dataset: Union[str, Dataset], topk: Optional[int] = 1
    ) -> Union[Tuple[List, List], pd.DataFrame]:

        assert (
            self.p_embedding is not None
        ), 

        if isinstance(query_or_dataset, str):
            doc_scores, doc_indices = self.get_relevant_doc(
                query_or_dataset, k=topk
            )
            print("[Search query]\n", query_or_dataset, "\n")

            for i in range(topk):
                print(f"Top-{i+1} passage with score {doc_scores[i]:4f}")
                print(self.contexts[doc_indices[i]])

            return (
                doc_scores,
                [self.contexts[doc_indices[i]] for i in range(topk)],
            )

        elif isinstance(query_or_dataset, Dataset):

            # Retrieve한 Passage를 pd.DataFrame으로 반환합니다.
            total = []
            with timer("query exhaustive search"):
                doc_scores, doc_indices = self.get_relevant_doc_bulk(
                    query_or_dataset["question"], k=topk
                )
            for idx, example in enumerate(
                tqdm(query_or_dataset, desc="Sparse retrieval: ")
            ):
                tmp = {
                    # Query와 해당 id를 반환합니다.
                    "question": example["question"],
                    "id": example["id"],
                    # Retrieve한 Passage의 id, context를 반환합니다.
                    "context_id": doc_indices[idx],
                    "context": " ".join(
                        [self.contexts[pid] for pid in doc_indices[idx]]
                    ),
                }
                if "context" in example.keys() and "answers" in example.keys():
                    # validation 데이터를 사용하면 ground_truth context와 answer도 반환합니다.
                    tmp["original_context"] = example["context"]
                    tmp["answers"] = example["answers"]
                total.append(tmp)

            cqas = pd.DataFrame(total)
            return cqas

    def get_relevant_doc(
        self, query: str, k: Optional[int] = 1
    ) -> Tuple[List, List]:

        with timer("transform"):
            query_vec = self.tfidfv.transform([query])
        assert (
            np.sum(query_vec) != 0
        ), "오류가 발생했습니다. 이 오류는 보통 query에 vectorizer의 vocab에 없는 단어만 존재하는 경우 발생합니다."

        with timer("query ex search"):
            result = query_vec * self.p_embedding.T
        if not isinstance(result, np.ndarray):
            result = result.toarray()

        sorted_result = np.argsort(result.squeeze())[::-1]
        doc_score = result.squeeze()[sorted_result].tolist()[:k]
        doc_indices = sorted_result.tolist()[:k]
        return doc_score, doc_indices

    def get_relevant_doc_bulk(
        self, queries: List, k: Optional[int] = 1
    ) -> Tuple[List, List]:

        """
        Arguments:
            queries (List):
                하나의 Query를 받습니다.
            k (Optional[int]): 1
                상위 몇 개의 Passage를 반환할지 정합니다.
        Note:
            vocab 에 없는 이상한 단어로 query 하는 경우 assertion 발생 (예) 뙣뙇?
        """

        query_vec = self.tfidfv.transform(queries)
        assert (
            np.sum(query_vec) != 0
        ), "오류가 발생했습니다. 이 오류는 보통 query에 vectorizer의 vocab에 없는 단어만 존재하는 경우 발생합니다."

        result = query_vec * self.p_embedding.T
        if not isinstance(result, np.ndarray):
            result = result.toarray()
        doc_scores = []
        doc_indices = []
        for i in range(result.shape[0]):
            sorted_result = np.argsort(result[i, :])[::-1]
            doc_scores.append(result[i, :][sorted_result].tolist()[:k])
            doc_indices.append(sorted_result.tolist()[:k])
        return doc_scores, doc_indices

    def retrieve_faiss(
        self, query_or_dataset: Union[str, Dataset], topk: Optional[int] = 1
    ) -> Union[Tuple[List, List], pd.DataFrame]:
        

        assert self.indexer is not None, "build_faiss()를 먼저 수행해주세요."

        if isinstance(query_or_dataset, str):
            doc_scores, doc_indices = self.get_relevant_doc_faiss(
                query_or_dataset, k=topk
            )
            print("[Search query]\n", query_or_dataset, "\n")

            for i in range(topk):
                print("Top-%d passage with score %.4f" % (i + 1, doc_scores[i]))
                print(self.contexts[doc_indices[i]])

            return (
                doc_scores,
                [self.contexts[doc_indices[i]] for i in range(topk)],
            )

        elif isinstance(query_or_dataset, Dataset):

            # Retrieve한 Passage를 pd.DataFrame으로 반환합니다.
            queries = query_or_dataset["question"]
            total = []

            with timer("query faiss search"):
                doc_scores, doc_indices = self.get_relevant_doc_bulk_faiss(
                    queries, k=topk
                )
            for idx, example in enumerate(
                tqdm(query_or_dataset, desc="Sparse retrieval: ")
            ):
                tmp = {
                    # Query와 해당 id를 반환합니다.
                    "question": example["question"],
                    "id": example["id"],
                    # Retrieve한 Passage의 id, context를 반환합니다.
                    "context_id": doc_indices[idx],
                    "context": " ".join(
                        [self.contexts[pid] for pid in doc_indices[idx]]
                    ),
                }
                if "context" in example.keys() and "answers" in example.keys():
                    # validation 데이터를 사용하면 ground_truth context와 answer도 반환합니다.
                    tmp["original_context"] = example["context"]
                    tmp["answers"] = example["answers"]
                total.append(tmp)

            return pd.DataFrame(total)

    def get_relevant_doc_faiss(
        self, query: str, k: Optional[int] = 1
    ) -> Tuple[List, List]:


        query_vec = self.tfidfv.transform([query])
        assert (
            np.sum(query_vec) != 0
        ), "오류가 발생했습니다. 이 오류는 보통 query에 vectorizer의 vocab에 없는 단어만 존재하는 경우 발생합니다."

        q_emb = query_vec.toarray().astype(np.float32)
        with timer("query faiss search"):
            D, I = self.indexer.search(q_emb, k)

        return D.tolist()[0], I.tolist()[0]

    def get_relevant_doc_bulk_faiss(
        self, queries: List, k: Optional[int] = 1
    ) -> Tuple[List, List]:

        query_vecs = self.tfidfv.transform(queries)
        assert (
            np.sum(query_vecs) != 0
        ), "오류가 발생했습니다. 이 오류는 보통 query에 vectorizer의 vocab에 없는 단어만 존재하는 경우 발생합니다."

        q_embs = query_vecs.toarray().astype(np.float32)
        D, I = self.indexer.search(q_embs, k)

        return D.tolist(), I.tolist()

class ElasticRetrieval:
    def __init__(self, INDEX_NAME):
        self.es, self.index_name = es_setting(index_name=INDEX_NAME) 

    def retrieve(
        self, query_or_dataset: Union[str, Dataset], topk: Optional[int] = 1
    ) -> Union[Tuple[List, List], pd.DataFrame]:
        if isinstance(query_or_dataset, str):
            doc_scores, doc_indices, docs = self.get_relevant_doc(query_or_dataset, k=topk)
            print("[Search query]\n", query_or_dataset, "\n")

            for i in range(min(topk, len(docs))):
                print(f"Top-{i+1} passage with score {doc_scores[i]:4f}")
                print(doc_indices[i])
                print(docs[i]['_source']['document_text'])

            return (doc_scores, [doc_indices[i] for i in range(topk)])

        elif isinstance(query_or_dataset, Dataset):
            # Retrieve한 Passage를 pd.DataFrame으로 반환합니다.
            total = []
            with timer("query exhaustive search"):
                doc_scores, doc_indices, docs = self.get_relevant_doc_bulk(
                    query_or_dataset["question"], k=topk
                )

            for idx, example in enumerate(tqdm(query_or_dataset, desc="Sparse retrieval with Elasticsearch: ")):
                # retrieved_context 구하는 부분 수정
                retrieved_context = []
                for i in range(min(topk, len(docs[idx]))):
                    retrieved_context.append(docs[idx][i]['_source']['document_text'])

                tmp = {
                    # Query와 해당 id를 반환합니다.
                    "question": example["question"],
                    "id": example["id"],
                    # Retrieve한 Passage의 id, context를 반환합니다.
                    "context_id": doc_indices[idx],
                    "context": " ".join(retrieved_context),  # 수정
                }
                if "context" in example.keys() and "answers" in example.keys():
                    # validation 데이터를 사용하면 ground_truth context와 answer도 반환합니다.
                    tmp["original_context"] = example["context"]
                    tmp["answers"] = example["answers"]
                total.append(tmp)

            cqas = pd.DataFrame(total)
            return cqas
    
    def retrieve_split(self, query_or_dataset: Union[str, Dataset], topk: Optional[int] = 1):
        with timer("query exhaustive search"):
            doc_scores, doc_indices, docs = self.get_relevant_doc_bulk(
                query_or_dataset["question"], k=topk
            )
        total = []
        for idx, example in enumerate(tqdm(query_or_dataset, desc="Sparse retrieval with Elasticsearch: ")):
            # retrieved_context 구하는 부분 수정
            retrieved_context = []
            for i in range(min(topk, len(docs[idx]))):
                retrieved_context.append(docs[idx][i]['_source']['document_text'])
            for i, context in enumerate(retrieved_context):
                tmp = {
                    # Query와 해당 id를 반환합니다.
                    "question": example["question"],
                    "id": doc_indices[i],
                    # Retrieve한 Passage의 id, context를 반환합니다.
                    "context_id": doc_indices[i],
                    "context": context
                }
                if "context" in example.keys() and "answers" in example.keys():
                    # validation 데이터를 사용하면 ground_truth context와 answer도 반환합니다.
                    tmp["original_context"] = example["context"]
                    tmp["answers"] = example["answers"]
                total.append(tmp)
            cqas = pd.DataFrame(total)
            return cqas

    
    def get_relevant_doc(self, query: str, k: Optional[int] = 1) -> Tuple[List, List]:
        doc_score = []
        doc_index = []
        res = es_search(self.es, self.index_name, query, k)
        docs = res['hits']['hits']

        for hit in docs:
            doc_score.append(hit['_score'])
            doc_index.append(hit['_id'])
            print("Doc ID: %3r  Score: %5.2f" % (hit['_id'], hit['_score']))

        return doc_score, doc_index, docs
    
    def get_relevant_doc_bulk(self, queries: List, k: Optional[int] = 1) -> Tuple[List, List]:
        total_docs = []
        doc_scores = []
        doc_indices = []

        for query in queries:
            doc_score = []
            doc_index = []
            res = es_search(self.es, self.index_name, query, k)
            docs = res['hits']['hits']

            for hit in docs:
                doc_score.append(hit['_score'])
                doc_indices.append(hit['_id'])

            doc_scores.append(doc_score)
            doc_indices.append(doc_index)
            total_docs.append(docs)

        return doc_scores, doc_indices, total_docs


if __name__ == "__main__":

    import argparse
    from datasets import load_dataset
    from transformers import AutoTokenizer

    parser = argparse.ArgumentParser(description="")
    parser.add_argument("--dataset_name", default="../data/train_dataset", type=str, help="")
    parser.add_argument("--use_faiss", default=False, type=bool, help="")
    parser.add_argument("--index_name", default="origin-meeting-wiki", type=str, help="테스트할 index name을 설정해주세요")

    args = parser.parse_args()

    # Test sparse
    org_datasets = load_dataset('csv', data_files={'validation': '../data/test.csv'})
    full_ds = org_datasets["validation"]
    print("*" * 40, "query dataset", "*" * 40)
    print(org_datasets)

    # 테스트 데이터 full_ds에도 동일하게 전처리
    post_context = [preprocess(text) for text in full_ds["context"]]
    post_question = [preprocess(text) for text in full_ds["question"]]

    # 기존의 전처리 이전 컬럼 삭제
    full_ds = full_ds.remove_columns("context")
    full_ds = full_ds.remove_columns("question")

    # 동일한 이름의 컬럼에 전처리 이후 데이터 추가
    full_ds = full_ds.add_column("context", post_context)
    full_ds = full_ds.add_column("question", post_question)

    # Elasticsearch 테스트
    retriever = ElasticRetrieval(args.index_name)

    query = "세희가 오늘 3시 40분에 곱창을 먹은 장소는?"

    if args.use_faiss:

        # test single query
        with timer("single query by faiss"):
            scores, indices = retriever.retrieve_faiss(query)

        # test bulk
        with timer("bulk query by exhaustive search"):
            df = retriever.retrieve_faiss(full_ds)
            df["correct"] = df["original_context"] == df["context"]

            print("correct retrieval result by faiss", df["correct"].sum() / len(df))

    else:
        with timer("bulk query by exhaustive search"):
            df = retriever.retrieve(full_ds, topk=6)
            df["correct"] = [original_context in context for original_context,context in zip(df["original_context"],df["context"])]
            print(
                "correct retrieval result by exhaustive search",
                f"{df['correct'].sum()}/{len(df)}",
                df["correct"].sum() / len(df),
            )
