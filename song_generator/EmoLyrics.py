import tensorflow as tf
import numpy as np
from tensorflow.contrib.rnn.python.ops.core_rnn_cell_impl import LSTMStateTuple, _LSTMStateTuple
from tensorflow.contrib import rnn
from tensorflow.contrib import legacy_seq2seq
from tensorflow.contrib.legacy_seq2seq import rnn_decoder
import random
import argparse
import pdb
import collections
import time
import process
import pdb
import pickle
from random import sample
import csv

# example
# TRAIN emotion: python3 EmoLyrics.py -p TRAIN -t emotion -fn jay_lyrics_notag_withEMO.csv 
# TRAIN baseline: python3 EmoLyrics.py -p TRAIN -t baseline -fn jay_lyrics_notag_withEMO.csv
# USE emotion:  python3 EmoLyrics.py -p USE -t emotion
# USE baseline:  python3 EmoLyrics.py -p USE -t baseline

def set_argparse():
    parser = argparse.ArgumentParser()
    parser.add_argument("-p","--purpose", help="purpose: TRAIN, USE", type=str)
    parser.add_argument("-t","--type", help="type: emotion, baseline", type=str)    
    parser.add_argument("-fn", "--filename", help="name: training file", type=str)
    args = parser.parse_args()
    return args

# data generator for emotion lyrics model
class ELM_DataGenerator():

    def __init__(self, datafiles, args):
        self.seq_length = 30
        self.batch_size = 100
        
        self.sentence = self.GetSentence(datafiles)
        self.Emo = np.load(datafiles.replace('.csv', '.npy'))
        
        self.total_len = len(self.sentence)
        self.words = list(self.CharSet(self.sentence))
        self.words = ["", "go", "unk"] + self.words
        # vocabulary
        self.vocab_size = len(self.words)  # vocabulary size
        # print('Vocabulary Size: ', self.vocab_size)
        self.char2id_dict = {w: i for i, w in enumerate(self.words)}
        self.id2char_dict = {i: w for i, w in enumerate(self.words)}
        # save char2id_dict, id2char_dict
        if args.type == 'emotion':
            self.SaveObj(self.char2id_dict, 'emo_char2id_dict')
            self.SaveObj(self.id2char_dict, 'emo_id2char_dict')
        elif args.type == 'baseline':
            self.SaveObj(self.char2id_dict, 'base_char2id_dict')
            self.SaveObj(self.id2char_dict, 'base_id2char_dict')           
        # pointer position to generate current batch
        self._pointer = 0
    
    def SaveObj(self, obj, name):
        with open('CKPT/song_generator/dict/' + name +'_'+ time.strftime("%Y%m%d-%H%M%S") + '.pickle', 'wb') as f:
            pickle.dump(obj, f, pickle.HIGHEST_PROTOCOL)
    
    def LoadObj(name, date):
        with open('CKPT/song_generator/dict/' + name +'_'+ date +'.pickle', 'rb') as f: 
            return pickle.load(f)      

    def GetSentence(self, csvpath):
        with open(csvpath, 'r', encoding='utf-8') as f:
            csvfile = csv.reader(f)
            sentence = []
            for row in csvfile:
                if row[1] != "lyrics":
                    sentence.append(row[1])
        return sentence
    
    def CharSet(self, LineList):
        AllWord = ''
        for line in LineList:
            AllWord += line
        return set(AllWord)

    def char2id(self, c):
        return self.char2id_dict[c]

    def id2char(self, id):
        return self.id2char_dict[id]

    def next_batch(self):
        x_batches = []
        x_emo_batches = []
        y_batches = []
        for i in range(self.batch_size):
            if self._pointer + self.batch_size >= self.total_len:
                self._pointer = 0
            x_sentence = self.sentence[self._pointer + i]
            y_sentence = self.sentence[self._pointer + i + 1]

            # convert to index
            x_idx = [self.char2id(c) for c in x_sentence]
            if len(x_idx) < 30:
                x_idx += [0 for i in range(self.seq_length - len(x_idx))]
            else:
                x_idx = x_idx[0: 30] 
            y_idx = [self.char2id(c) for c in y_sentence]
            if len(y_idx) < 30:
                y_idx += [0 for i in range(self.seq_length - len(y_idx))]
            else:
                y_idx = y_idx[0: 30] 
            
            x_batches.append(x_idx)
            y_batches.append(y_idx)
        bx_emo = self.Emo[self._pointer: self._pointer + self.batch_size]
        self._pointer += self.batch_size
        return x_batches, y_batches, bx_emo 

class EmoLyricsModel(object):
    def __init__(self, args):    
        # info in args: type, purpose, 
        # type: emotion, baseline
        # purpose: TRAIN, USE
        self.model_type = args.type
        self.purpose = args.purpose
        self.flag_test = False if self.purpose == 'TRAIN' else True
        
        # model's variable
        if self.model_type == 'emotion':
            self.NumClass = 7
        elif self.model_type == 'baseline':
            self.NumClass = 0        
        self.SizeVocab = 2262
        self.NumLayer = 2
        self.lr = 0.01 #learning rate
        self.NumInput = 30
        self.SizeBatch = 100
        self.NumHidden = 256

        # create graph
        self.x_input, self.y_input, self.x_emo_dist, \
        self.target, self.weight, self.bias, self.embedding = self.Model_init()
        self.Model_Main()
 
    # initialize: x, y x_emo_dist, target, weight, bias, embedding
    def Model_init(self):
        x_input = tf.placeholder(tf.int32, [None, self.NumInput]) # x
        y_input = tf.placeholder(tf.int32, [None, self.NumInput]) # y
        # emotion distribution of x_input
        x_emo_dist = tf.placeholder(tf.float32, [None, self.NumClass])         
        target = tf.cast(y_input, tf.int32)
        
        # weights
        weight = {
        'EmoDecoderOut': tf.Variable(tf.random_normal([self.NumHidden + self.NumClass,\
            self.SizeVocab])),\
        'BaseDecoderOut': tf.Variable(tf.random_normal([self.NumHidden,\
            self.SizeVocab]))
                  }
        # biases
        bias = {
        'EmoDecoderOut': tf.Variable(tf.random_normal([self.SizeVocab])),
        'BaseDecoderOut': tf.Variable(tf.random_normal([self.SizeVocab]))
                 }
        embedding = tf.get_variable("embedding", [self.SizeVocab, self.NumHidden])

        return x_input, y_input, x_emo_dist, target, weight, bias, embedding
    
    def seq2seq_encoder(self, encoder_input, layer):
        encoder_input = tf.nn.embedding_lookup(self.embedding, encoder_input)
        encoder_input = tf.split(encoder_input, self.NumInput, 1)
        encoder_input = [tf.squeeze(input_, [1]) for input_ in encoder_input]

        rnn_cell = rnn.MultiRNNCell([rnn.BasicLSTMCell(self.NumHidden) for ly in range(layer)])
        outputs, states = rnn.static_rnn(rnn_cell, encoder_input, dtype=tf.float32)

        return outputs, states
    
    def loop(self, prev, _):
        prev = tf.matmul(prev, self.weight['EmoDecoderOut']) + self.bias['EmoDecoderOut']
        prev_symbol = tf.stop_gradient(tf.argmax(prev, 1))
        prev_symbol = tf.cast(prev_symbol, tf.int32)
        return tf.nn.embedding_lookup(self.embedding, prev_symbol)    

    def seq2seq_decoder(self, decoder_input, initial_state, layer):
        # input_go = tf.ones([decoder_input.shape[1].value, 1]) # change
        input_go = tf.ones([1, 1])
        input_go = tf.cast(input_go, tf.int32)
        decoder_input_batchsize = tf.shape(decoder_input)[0]
        input_go = tf.tile(input_go, tf.stack([decoder_input_batchsize, 1]))
        # pdb.set_trace()
        decoder_input = tf.concat([input_go, decoder_input], 1)
        decoder_input = tf.nn.embedding_lookup(self.embedding, decoder_input)
        decoder_input = tf.split(decoder_input, self.NumInput+1, 1)
        decoder_input = [tf.squeeze(input_, [1]) for input_ in decoder_input]

        decoder_rnn_cell = rnn.MultiRNNCell([rnn.BasicLSTMCell(self.NumHidden + self.NumClass) for ly in range(self.NumLayer)])
        # pdb.set_trace()
        outputs, states = rnn_decoder(decoder_input, initial_state, decoder_rnn_cell, loop_function=self.loop if self.flag_test else None)
        return outputs, states
    
    def modified_state(self, state, emo_state):
        final_tuple_state =()
        for ly_state in state:
            original_state = ly_state
            original_memory_state = original_state.c
            original_hidden_state = original_state.h
            
            new_h = tf.concat([original_hidden_state, emo_state], 1)
            new_c = tf.concat([original_memory_state, emo_state], 1)
            new_tuple_state = LSTMStateTuple(new_c, new_h)
            final_tuple_state += (new_tuple_state,)
        return final_tuple_state
    
    def Model_Main(self):
        EncoOutput, EncoState = self.seq2seq_encoder(self.x_input, layer= self.NumLayer)
        if self.model_type == 'emotion':
            EncoState_m = self.modified_state(EncoState, self.x_emo_dist) # modified states
            DecoOutput, DecoState = self.seq2seq_decoder(self.y_input, EncoState_m, layer= self.NumLayer)
        elif self.model_type == 'baseline':
            DecoOutput, DecoState = self.seq2seq_decoder(self.y_input, EncoState, layer= self.NumLayer)
         # get first 30 words
        DecoOutput = DecoOutput[0:-1]
        # reshape decoder output
        DecoOutput_r = tf.reshape(tf.concat(DecoOutput, 1), [-1, self.NumHidden+ self.NumClass])
        DecoLogit = tf.matmul(DecoOutput_r, self.weight['EmoDecoderOut']) + self.bias['EmoDecoderOut']

        self.DecoProb = tf.nn.softmax(DecoLogit)
        self.DecoWord_idx = tf.argmax(self.DecoProb, 1)

        loss = legacy_seq2seq.sequence_loss_by_example(
                        [DecoLogit],
                        [tf.reshape(self.target, [-1])],
                        [tf.ones([self.SizeBatch*self.NumInput])])

        self.cost = tf.reduce_mean(loss)
        self.optimizer = tf.train.AdamOptimizer(learning_rate=self.lr).minimize(self.cost)


def ELM_train(model, data, args):
    # save path
    if args.type == 'emotion':
        save_model_path = 'CKPT/song_generator/emo_lyrics_model_'+ time.strftime("%Y%m%d-%H%M%S")
    elif args.type == 'baseline':
        save_model_path = 'CKPT/song_generator/base_lyrics_model_'+ time.strftime("%Y%m%d-%H%M%S")
    
    # training variables 
    epochs = 100
    number_of_epoch = 0
    init = tf.global_variables_initializer()
    saver = tf.train.Saver(max_to_keep=6)
    gpu_options = tf.GPUOptions(per_process_gpu_memory_fraction=0.333)
    session = tf.Session(config=tf.ConfigProto(gpu_options=gpu_options))
    session.run(init)
    
    print("START TRAINING!!")
    while number_of_epoch < epochs:
        cost_total = 0
        num_of_batch_per_epoch = int(data.total_len//data.batch_size)

        # train:
        print('EPOCH:{} start!'.format(number_of_epoch))
        for number_of_batch in range(num_of_batch_per_epoch):
            if args.type == 'emotion':
                # generate data from ELM_DataGenerator
                x_train, y_train, x_emo_train = data.next_batch()
                feed_dict = {  
                            model.x_input: x_train, 
                            model.y_input: y_train, 
                            model.x_emo_dist: x_emo_train
                            }
            elif args.type == 'baseline':
                x_train, y_train, _ = data.next_batch()
                feed_dict = {  
                            model.x_input: x_train, 
                            model.y_input: y_train, 
                            }            
            _, cost = session.run([model.optimizer, model.cost], feed_dict )
            cost_total += cost
            print("cost:", cost)
        print('EPOCH:{}, training_loss:{:4f}'.format(number_of_epoch, \
                                                     cost_total/num_of_batch_per_epoch ))
        if number_of_epoch%15 == 0:
            saver.save(session, save_model_path, global_step=number_of_epoch)
        number_of_epoch += 1

def BeamSearch(arr, candidate):
    candidate_idx = arr.argsort()[-3:][::-1]


def ELM_sample(model, model_num, date):
    char2id = ELM_DataGenerator.LoadObj('emo_char2id_dict', date)
    id2char = ELM_DataGenerator.LoadObj('emo_id2char_dict', date)
    # pdb.set_trace()
    saver = tf.train.Saver()
    with tf.Session() as session:
        # ckpt = tf.train.latest_checkpoint('CKPT/song_generator/')
        # model(good performance): 30, 45, 75 epochs
        # model(over fitting): 60, 90 epochs
        ckpt = 'CKPT/song_generator/emo_lyrics_model_'+ date + '-' + model_num 
        print(ckpt)
        saver.restore(session, ckpt)
        sentence = u'你要离开我知道很简单'
        sentence_ = [char2id[c] for c in sentence]
        sentence_ += [0 for i in range( model.NumInput - len(sentence))]

        y_fake = [0 for i in range(model.NumInput)]
        # 0.0733089,0.228538,2.36306e-09,0.258303,0.188573,0.251277,2.32927e-08
        sentence_emo = np.array([0.0733089,0.228538,2.36306e-09,0.258303,0.188573,0.251277,2.32927e-08])
        
        feed_dict = {
                     model.x_input: [sentence_],\
                     model.y_input: [y_fake],\
                     model.x_emo_dist: [sentence_emo]
                    }
        output_softmax = session.run([model.DecoProb[0]], feed_dict)
        idx = output_softmax[0].argsort()[-3:][::-1]

        CandidateIdx = [[i] for i in idx]
        CandidateProb = [np.log(i) for i in output_softmax[0][idx]]
        for num_char in range(30):
            prob_list = []
            candidate_list = []            
            for num in range(3):
                y_fake = [ CandidateIdx[num][-1] ] + [0 for i in range(model.NumInput - 1)]
                feed_dict[model.y_input] = [y_fake]
                output_softmax = session.run([model.DecoProb[1]], feed_dict)
                idx = output_softmax[0].argsort()[-3:][::-1]
                prob = output_softmax[0][idx]
                for num_ in range(3):
                    prob_list.append(np.log(prob[num_]) + CandidateProb[num])
                    candidate_list.append(CandidateIdx[num] + [idx[num_]])
            top_idx = sorted(range(len(prob_list)), key=lambda i: prob_list[i])[-3:]
            CandidateIdx = [candidate_list[i] for i in top_idx]
            CandidateProb = [prob_list[i] for i in top_idx]
          
        # output_sentence = [id2char[c] for c in output_idx[0]]
        # output_sentence_w = ''.join(output_sentence)
        # print(sentence)
        # print(sentence_)
        # print(output_idx)
        # print(output_sentence_w)
    return 0

def BASE_sample(model, model_num, date):
    char2id = ELM_DataGenerator.LoadObj('emo_char2id_dict', date)
    id2char = ELM_DataGenerator.LoadObj('emo_id2char_dict', date)
    # pdb.set_trace()
    saver = tf.train.Saver()
    with tf.Session() as session:
        # ckpt = tf.train.latest_checkpoint('CKPT/song_generator/')
        # model(good performance): 30, 45, 75 epochs
        # model(over fitting): 60, 90 epochs
        ckpt = 'CKPT/song_generator/base_lyrics_model_'+ date + '-' + model_num 
        print(ckpt)
        saver.restore(session, ckpt)
        for line_num in range(30):
            if line_num == 0:
                sentence = u'很简单'
            sentence_ = [char2id[c] for c in sentence]
            sentence_ += [0 for i in range( model.NumInput - len(sentence))]

            y_fake = [0 for i in range(model.NumInput)]
            feed_dict = {
                         model.x_input: [sentence_],
                         model.y_input: [y_fake],
                        }
            output_idx = session.run([model.DecoWord_idx], feed_dict)
            output_sentence = [id2char[c] for c in output_idx[0]]
            output_sentence_w = ''.join(output_sentence)
            sentence = output_sentence_w
            print(output_sentence_w)
    return 0

if __name__ == '__main__':
    args = set_argparse()
    with tf.variable_scope('song_generator'):
        Model = EmoLyricsModel(args)

    if args.purpose == 'TRAIN':
        path = 'TEST/' + args.filename 
        TrainData = ELM_DataGenerator(path, args)
        ELM_train(Model, TrainData, args)
    
    if args.purpose == 'USE':
        date = input("DATE-TIME (form:20120515-155045):")
        model_num = input("Model's Num:")
        if args.type == 'emotion':
            ELM_sample(Model, model_num, date)
        elif args.type == 'baseline':
            BASE_sample(Model, model_num, date)