import json
import pprint
import warnings
import re
import os
import argparse
from tqdm import tqdm
from elasticsearch import Elasticsearch

warnings.filterwarnings('ignore')

def es_setting(index_name="origin-meeting-wiki"):
    es = Elasticsearch('http://localhost:9200', timeout=30, max_retries=10, retry_on_timeout=True)
    print("Ping Elasticsearch :", es.ping())

    return es, index_name

def create_index(es, index_name, setting_path = "./setting.json"):
    with open(setting_path, "r") as f:
        setting = json.load(f)
    es.indices.create(index=index_name, body=setting)
    print("Index creation has been completed")

def delete_index(es, index_name):
    if es.indices.exists(index=index_name):
        es.indices.delete(index=index_name)
        print("Deleting index {} ...".format(index_name))
    else:
        print("Index {} does not exist.".format(index_name))

def delete_doc(es, index_name, doc_id):
    deleted_doc = es.get(index=index_name, id=doc_id)

    if es.exists(index=index_name, id=doc_id):
        es.delete(index=index_name, id=doc_id)
        print("Deleting id {} from index {} ...".format(doc_id, index_name))
    else:
        print("Id {} does not existin index {}.".format(doc_id, index_name))

    return deleted_doc['_source']['document_text']

def initial_index(es, index_name, setting_path = "./setting.json"):
    delete_index(es, index_name)
    create_index(es, index_name, setting_path)

def preprocess(text):
    text = re.sub(r"\n", " ", text)
    text = re.sub(r"\\n", " ", text)
    text = re.sub(r"#", " ", text)
    text = re.sub(r"[^A-Za-z0-9가-힣.?!,()~‘’“”"":%&《》〈〉''㈜·\-\'+\s一-龥]", "", text)
    text = re.sub(r"\s+", " ", text).strip()  # 두 개 이상의 연속된 공백을 하나로 치환
    
    return text

def load_json(dataset_path):
    with open(dataset_path, "r") as f:
        wiki = json.load(f)

    texts = list(dict.fromkeys([v["text"] for v in wiki.values()]))
    texts = [preprocess(text) for text in texts]
    corpus = [
        {"document_text": texts[i]} for i in range(len(texts))
    ]
    return corpus

def load_txt(folder):
    """
    사용자가 추가한 폴더의 모든 txt파일 로드
    """
    # folder = "../data/new_data/"
    file_list = os.listdir(folder)
    file_list_txt = [file for file in file_list if file.endswith(".txt")]

    texts = []
    for file in file_list_txt:
        path = os.path.join(folder, file)
        f = open(path, "r")
        data = f.read()
        f.close()
        texts.append(data)

    texts = [preprocess(text) for text in texts]
    corpus = [
        {"document_text": texts[i]} for i in range(len(texts))
    ]
    
    return corpus

def insert_data(es, index_name, dataset_path, type="json", start_id=None):
    if type == "json":
        corpus = load_json(dataset_path)
    elif type == "txt":
        corpus = load_txt(dataset_path)

    for i, text in enumerate(tqdm(corpus)):
        try:
            if isinstance(start_id, int):
                es.index(index=index_name, id=start_id+i, body=text)    
            else:
                es.index(index=index_name, id=i, body=text)
        except:
            print(f"Unable to load document {i}.")

    n_records = count_doc(es, index_name=index_name)
    print(f"Succesfully loaded {n_records} into {index_name}")
    print("@@@@@@@ 데이터 삽입 완료 @@@@@@@")

def insert_data_st(es, index_name, corpus, titles, start_id=None):
    for i, text in enumerate(tqdm(corpus)):
        try:
            if isinstance(start_id, int):
                es.index(index=index_name, id=start_id+i, body=text)    
            else:
                es.index(index=index_name, id=titles[i], body=text)
        except:
            print(f"Unable to load document {i}.")

    n_records = count_doc(es, index_name=index_name)
    print(f"Succesfully loaded {n_records} into {index_name}")

def read_uploadedfile(files):
    texts = []
    titles = []
    for file in files:
        title = file.name.split(".")[0]
        text = file.read().decode('utf-8')
        texts.append(title + " " + preprocess(text))
        titles.append(title)
    
    corpus = [
        {"document_text": texts[i]} for i in range(len(texts))
    ]

    return corpus, titles

def update_doc(es, index_name, doc_id, data_path):
    f = open(data_path, "r")  # 수정할 텍스트
    text = f.read()
    new_text = {"document_text" : text}
    es.update(es, index=index_name, id=doc_id, doc=new_text)
    
    print(f"Succesfully updated doc {doc_id} in {index_name}")

def count_doc(es, index_name):
    n_records = es.count(index=index_name)["count"]

    return n_records

def check_data(es, index_name, doc_id=0):
    doc = es.get(index=index_name, id=doc_id)

    return doc['_source']['document_text']

def es_search(es, index_name, question, topk):
    query = {
        "query": {
            "bool": {
                "must": [
                    {"match": {"document_text": question}}
                ]
            }
        }
    }

    res = es.search(index=index_name, body=query, size=topk)
    return res

def search_all(es, index_name):
    query = {
        "query": {
            "match_all": {}
        }
    }

    res = es.search(index=index_name, body=query, size = es.count(index=index_name)["count"])

    return res

def check_index(es, user_index):
    indices=sorted(es.indices.get_alias().keys())
    flag = True if user_index in indices else False

    return flag, indices

def user_setting(es, index_name, corpus, titles, type="first", setting_path = "./setting.json"):
    if type == "first":
        # 첫 번째 사용하는 경우
        initial_index(es, index_name, setting_path=setting_path)
        insert_data_st(es, index_name, corpus, titles)
        doc_num = count_doc(es, index_name=index_name)  # 기존에 존재하는 doc 개수가 출력됨
        print("첫 번째 사용하는 경우")
        print("doc 개수: ", doc_num)

    elif type == "second":
        # 두 번째 사용하는 경우
        doc_num = count_doc(es, index_name)  # 또 여기서는 잘 작동함
        insert_data_st(es, index_name, corpus, titles, start_id=doc_num)
        print("두 번째 사용하는 경우")
        print("doc 개수: ", doc_num)


def main(args):
    """
    모델 inference 에서 사용
    """
    es, index_name = es_setting(index_name=args.index_name)
    initial_index(es, index_name, args.setting_path)
    insert_data(es, index_name, args.dataset_path, type="json")

    query = "오늘 3시 40분에 세희랑 밥 먹은 사람 누구야?"
    res = es_search(es, index_name, query, 10)
    print("========== RETRIEVE RESULTS ==========")
    pprint.pprint(res)

    print('\n=========== RETRIEVE SCORES ==========\n')
    for hit in res['hits']['hits']:
        print("Doc ID: %3r  Score: %5.2f" % (hit['_id'], hit['_score']))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="")
    parser.add_argument("--setting_path", default="./setting.json", type=str, help="생성할 index의 setting.json 경로를 설정해주세요")
    parser.add_argument("--dataset_path", default="../data/meeting_collection.json", type=str, help="삽입할 데이터의 경로를 설정해주세요")
    parser.add_argument("--index_name", default="origin-meeting-wiki", type=str, help="테스트할 index name을 설정해주세요")

    args = parser.parse_args()
    main(args)
