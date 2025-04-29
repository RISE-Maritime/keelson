import yaml
import os


# TODO: Find a way to generate the markdown file in the setup.py file



def generate_markdown_from_yaml(yaml_file, markdown_file):
    with open(yaml_file, 'r') as file:
        subjects = yaml.safe_load(file)

    with open(markdown_file, 'w') as file:

        file.write(f"## Well-known subjects\n Organized by packages:\n\n")

        keelson_package = None

        for subject in subjects:

            if "keelson_package" in subjects[subject]:
                if keelson_package != subjects[subject]["keelson_package"]:
                    keelson_package = subjects[subject]["keelson_package"]

                    file.write(f"\n### {keelson_package.upper()}\n")
        
            if "schema" in subjects[subject]:
                file.write(f"- **{subject}** [{subjects[subject]['schema']}]")

            if 'path_to_message' in subjects[subject]:
                file.write(f"({subjects[subject]['path_to_message']})")


            file.write("\n")
            

            # message line
            print(f"**{subject}** [{subjects[subject]["schema"]}]()")


            # add link
            print(f"[Schema]({subjects[subject]['schema']})")
            # file.write(f"[Schema]({subjects[subject]['schema']})\n\n")

            # file.write(f"# {subject}\n\n")


if __name__ == "__main__":

    current_path = os.path.dirname(os.path.abspath(__file__))

    generate_markdown_from_yaml(
        f'{current_path}/subjects.yaml', f'{current_path}/README_subjects.md')
