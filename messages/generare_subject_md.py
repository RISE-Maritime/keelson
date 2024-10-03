import yaml
import os

# TODO: Add github link to the schema
# TODO: Find a way to generate the markdown file in the setup.py file
# TODO: Add file to docstring


def generate_markdown_from_yaml(yaml_file, markdown_file):
    with open(yaml_file, 'r') as file:
        subjects = yaml.safe_load(file)

    with open(markdown_file, 'w') as file:

        file.write(f"## Well-known subjects\n")

        category = None

        for subject in subjects:

            if "category" in subjects[subject]:
                if category != subjects[subject]["category"]:
                    category = subjects[subject]["category"]
                    file.write(f"\n### {category}\n")
        
            if "schema" in subjects[subject]:
                file.write(f"- {subject} [{subjects[subject]['schema']}]")

            if 'link' in subjects[subject]:
                file.write(f"({subjects[subject]['link']})")


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
        f'{current_path}/subjects.yaml', f'{current_path}/subjects.md')
