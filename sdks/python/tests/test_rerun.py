from keelson.mcap_file_reader import readMCAPiterator


def test_readMCAPiterator():
    filename = "tests/data/mcap/2024-10-01-12-00-00-0000.mcap"
    topics = ["aptiv_point_cloud"]
    reader = readMCAPiterator(filename, topics)
    for i, message in enumerate(reader):
        if i > 10:
            break
        print(message)
        

