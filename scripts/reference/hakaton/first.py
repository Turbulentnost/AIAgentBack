pip install rarfile
pip install opencv-python numpy
pip install pytesseract

import rarfile
import os
import cv2
import numpy as np
import pytesseract
import csv
from PIL import Image


def extract_images_from_rar(rar_path, output_dir):
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    with rarfile.RarFile(rar_path) as rf:
        for file_info in rf.infolist():
            if file_info.filename.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.bmp')):
                rf.extract(file_info, output_dir)
                print(f'Извлечено: {file_info.filename}')


rar_file_path = 'dataset.rar'
output_directory = 'papka'

extract_images_from_rar(rar_file_path, output_directory)

data = []
path_port = [f'file_{i:04}.jpg' for i in range(1, 1095)]
for i in path_port:

    def remove_distortion(image):

        focal_length = image.shape[1]
        center = (image.shape[1] / 2, image.shape[0] / 2)

        # Матрица камеры
        camera_matrix = np.array([[focal_length, 0, center[0]],
                                  [0, focal_length, center[1]],
                                  [0, 0, 1]], dtype=np.float32)

        distortion_coeffs = np.array([0.1, 0, 0, 0], dtype=np.float32)

        undistorted_image = cv2.undistort(image, camera_matrix, distortion_coeffs)

        return undistorted_image


    def preprocess_image(image_path, target_size=(1200, 1600)):

        image = cv2.imread('papka/' + image_path)

        if image is None:
            raise ValueError(f"Не удалось загрузить изображение по пути: {image_path}")

        undistorted_image = remove_distortion(image)

        gray_image = cv2.cvtColor(undistorted_image, cv2.COLOR_BGR2GRAY)

        kernel = np.array([[0.1, 0.15, 0.1],
                           [0.15, 1, 0.15],
                           [0.1, 0.15, 0.1]], dtype=np.float32)
        kernel = kernel / np.sum(kernel)

        filtered_image = cv2.filter2D(gray_image, ddepth=-1, kernel=kernel)

        binary_image = cv2.adaptiveThreshold(filtered_image, 255,
                                             cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                             cv2.THRESH_BINARY, 11, 2)

        resized_image = cv2.resize(binary_image, target_size)

        return resized_image


    cv2.imwrite('1' + i, preprocess_image(i))
try:
    for i in path_port:
        def extract_text_from_image(image_path):
            image = Image.open('1' + i)

            text = pytesseract.image_to_string(image, lang="rus", config="--psm 6 --oem 1")

            return text


        print(i)
        data.append([i, extract_text_from_image('1' + i)])
except ValueError as e:
    with open("data.csv", mode="w", encoding="windows-1251", newline="") as file:
        writer = csv.writer(file)
        writer.writerows(data)
    print("CSV-файл создан и данные записаны.")

with open("data.csv", mode="w", encoding="windows-1251", newline="") as file:
    writer = csv.writer(file)
    writer.writerows(data)
print("CSV-файл создан и данные записаны.")
