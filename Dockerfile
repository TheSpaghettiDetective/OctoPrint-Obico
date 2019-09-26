FROM kennethjiang/octopi:ubuntu

RUN apt-get install -y netcat

RUN pip install ipdb

COPY . /app

WORKDIR /app

RUN pip install -e ./

